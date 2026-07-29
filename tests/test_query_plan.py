from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.capabilities.sql_runner.models import RunSqlToolArgs  # noqa: E402
from QueryMind.core.agent.agent import (  # noqa: E402
    _append_query_plan_recovery_prompt,
    _is_query_plan_rejection,
    _query_plan_recovery_tools,
)
from QueryMind.core.agent.query_plan import (  # noqa: E402
    QueryPlan,
    QueryPlanFilter,
    build_schema_evidence,
    validate_query_plan_evidence,
    validate_query_plan_intent,
    validate_sql_against_query_plan,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory  # noqa: E402
from QueryMind.core.tool import ToolContext, ToolRejection, ToolSchema  # noqa: E402
from QueryMind.core.tool.models import ToolResult  # noqa: E402
from QueryMind.core.user import User  # noqa: E402
from QueryMind.rls_registry import RLSToolRegistry  # noqa: E402
from QueryMind.tools.query_plan import SubmitQueryPlanTool  # noqa: E402


def _user() -> User:
    return User(
        id="u1",
        username="tester",
        email="tester@example.com",
        group_memberships=["user"],
    )


def _context(metadata: dict | None = None) -> ToolContext:
    return ToolContext(
        user=_user(),
        conversation_id="c1",
        request_id="r1",
        raw_user_message="show product names",
        agent_memory=NoOpAgentMemory(),
        metadata=dict(metadata or {}),
    )


def _sales_plan(**overrides) -> QueryPlan:
    values = {
        "objective": "Total order value by territory",
        "source_tables": [
            "sales.salesorderheader",
            "sales.salesterritory",
        ],
        "required_columns": [
            "sales.salesorderheader.territoryid",
            "sales.salesorderheader.totaldue",
            "sales.salesterritory.territoryid",
            "sales.salesterritory.name",
        ],
        "metric_expressions": ["SUM(salesorderheader.totaldue)"],
        "dimensions": ["salesterritory.name"],
        "filters": [],
        "row_grain": "one row per sales territory",
        "output_columns": ["territory_name", "total_sales"],
        "join_path": [
            "salesorderheader.territoryid = salesterritory.territoryid"
        ],
        "requires_aggregation": True,
        "requires_grouping": True,
        "requires_ordering": True,
    }
    values.update(overrides)
    return QueryPlan(**values)


def _sales_evidence() -> dict:
    return {
        "schema_evidence": {
            "tables": [
                "adventureworks.sales.salesorderheader",
                "adventureworks.sales.salesterritory",
            ],
            "columns": [
                "adventureworks.sales.salesorderheader.territoryid",
                "adventureworks.sales.salesorderheader.totaldue",
                "adventureworks.sales.salesterritory.territoryid",
                "adventureworks.sales.salesterritory.name",
            ],
            "retrieval_count": 1,
        }
    }


def test_schema_evidence_merges_tables_and_columns_across_retrievals() -> None:
    first = build_schema_evidence(
        {},
        {
            "selected_tables": ["sales.orders"],
            "selected_column_refs": ["sales.orders.orderid"],
            "selected_columns": {"sales.orders": ["orderid"]},
        },
    )
    second = build_schema_evidence(
        {"schema_evidence": first},
        {
            "selected_tables": ["sales.orders", "sales.customers"],
            "selected_column_refs": [
                "sales.orders.customerid",
                "sales.customers.customerid",
            ],
            "selected_columns": {
                "sales.orders": ["customerid"],
                "sales.customers": ["customerid"],
            },
        },
    )

    assert second["tables"] == ["sales.orders", "sales.customers"]
    assert second["table_columns"]["sales.orders"] == ["orderid", "customerid"]
    assert second["retrieval_count"] == 2


def test_query_plan_requires_retrieved_qualified_evidence() -> None:
    plan = _sales_plan()
    accepted = validate_query_plan_evidence(plan, _sales_evidence())

    missing = validate_query_plan_evidence(
        plan,
        {
            "schema_evidence": {
                "tables": ["sales.salesorderheader"],
                "columns": ["sales.salesorderheader.totaldue"],
            }
        },
    )

    assert accepted.passed is True
    assert missing.passed is False
    assert any(issue.startswith("table_not_in_schema_evidence") for issue in missing.issues)
    assert any(issue.startswith("column_not_in_schema_evidence") for issue in missing.issues)


def test_query_plan_rejects_unrequested_distinct_count_of_single_table_pk() -> None:
    plan = QueryPlan(
        objective="Count monthly orders",
        source_tables=["sales.orders"],
        required_columns=["sales.orders.order_id", "sales.orders.order_date"],
        metric_expressions=["COUNT(DISTINCT orders.order_id) AS order_count"],
        dimensions=["DATE_TRUNC('month', orders.order_date)"],
        row_grain="one row per month",
        output_columns=["order_month", "order_count"],
        requires_aggregation=True,
        requires_grouping=True,
    )
    metadata = {
        "schema_evidence": {
            "table_primary_keys": {"warehouse.sales.orders": ["order_id"]}
        }
    }

    rejected = validate_query_plan_intent(
        plan,
        metadata,
        "Count orders for each month",
    )
    explicit = validate_query_plan_intent(
        plan,
        metadata,
        "Count unique orders for each month",
    )

    assert rejected.issues == [
        "unrequested_distinct_primary_key_count:orders.order_id"
    ]
    assert explicit.passed is True


def test_submit_query_plan_persists_only_accepted_plan() -> None:
    context = _context(_sales_evidence())
    tool = SubmitQueryPlanTool()

    result = asyncio.run(tool.execute(context, _sales_plan()))

    assert result.success is True
    assert context.metadata["query_plan_status"] == "accepted"
    assert context.metadata["query_plan"]["output_columns"] == [
        "territory_name",
        "total_sales",
    ]


def test_submit_query_plan_rejects_unresolved_business_question() -> None:
    context = _context(_sales_evidence())
    result = asyncio.run(
        SubmitQueryPlanTool().execute(
            context,
            _sales_plan(unresolved_questions=["Does total mean subtotal or total due?"]),
        )
    )

    assert result.success is False
    assert result.metadata["rejection_stage"] == "planning"
    assert result.metadata["rejection_code"] == "query_plan_evidence_gap"
    assert "unresolved_questions" in result.metadata["query_plan_issues"]


def test_sql_alignment_checks_tables_filters_features_and_output_count() -> None:
    plan = _sales_plan(
        filters=[
            QueryPlanFilter(
                column="sales.salesorderheader.orderdate",
                operator=">=",
                value_description="2024-01-01",
            )
        ],
        required_columns=_sales_plan().required_columns
        + ["sales.salesorderheader.orderdate"],
    )
    aligned_sql = """
        SELECT st.name AS territory_name, SUM(soh.totaldue) AS total_sales
        FROM sales.salesorderheader AS soh
        JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
        WHERE soh.orderdate >= DATE '2024-01-01'
        GROUP BY st.name
        ORDER BY total_sales DESC
    """
    drifted_sql = """
        SELECT st.territoryid, st.name, SUM(soh.totaldue) AS total_sales
        FROM sales.salesorderheader AS soh
        JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
        GROUP BY st.territoryid, st.name
        ORDER BY total_sales DESC
    """

    aligned = validate_sql_against_query_plan(plan, aligned_sql, dialect="postgres")
    drifted = validate_sql_against_query_plan(plan, drifted_sql, dialect="postgres")

    assert aligned.passed is True
    assert "output_column_count_mismatch:3!=2" in drifted.issues
    assert any(issue.startswith("planned_filter_missing_from_sql") for issue in drifted.issues)


def test_rls_registry_requires_and_enforces_accepted_query_plan() -> None:
    registry = RLSToolRegistry(require_query_plan=True)
    tool = SimpleNamespace(name="run_sql")
    context = _context({"dialect": "postgres"})
    args = RunSqlToolArgs(sql="SELECT p.name FROM production.product AS p")

    missing_plan = asyncio.run(
        registry.transform_args(tool, args, context.user, context)
    )
    assert isinstance(missing_plan, ToolRejection)
    assert missing_plan.code == "query_plan_required"

    context.metadata.update(
        {
            "query_plan_status": "accepted",
            "query_plan": QueryPlan(
                objective="Show product names",
                source_tables=["production.product"],
                required_columns=["production.product.name"],
                row_grain="one row per product",
                output_columns=["name"],
            ).model_dump(mode="json"),
        }
    )
    accepted = asyncio.run(
        registry.transform_args(tool, args, context.user, context)
    )

    assert isinstance(accepted, RunSqlToolArgs)


def test_query_plan_recovery_limits_visible_tools_and_can_exhaust() -> None:
    schemas = [
        ToolSchema(name=name, description=name, parameters={})
        for name in ["schema_retrieve", "submit_query_plan", "run_sql"]
    ]
    rejection = ToolResult(
        success=False,
        result_for_llm="rejected",
        metadata={"rejection_stage": "planning"},
    )

    assert _is_query_plan_rejection(rejection) is True
    assert [tool.name for tool in _query_plan_recovery_tools(schemas)] == [
        "schema_retrieve",
        "submit_query_plan",
    ]
    assert "temporarily unavailable" in _append_query_plan_recovery_prompt("base")
    exhausted_prompt = " ".join(
        _append_query_plan_recovery_prompt("base", exhausted=True).split()
    )
    assert "Do not call tools again" in exhausted_prompt
