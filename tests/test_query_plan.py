from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.capabilities.sql_runner.models import RunSqlToolArgs  # noqa: E402
from QueryMind.core.agent.agent import (  # noqa: E402
    _append_query_plan_recovery_prompt,
    _backfill_schema_retrieve_query,
    _is_query_plan_rejection,
    _query_plan_recovery_tools,
)
from QueryMind.core.agent.query_plan import (  # noqa: E402
    QueryPlan,
    QueryPlanFilter,
    QueryPlanMode,
    assess_query_plan_risk,
    build_schema_evidence,
    parse_query_plan_mode,
    validate_query_plan_evidence,
    validate_query_plan_intent,
    validate_sql_against_query_plan,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory  # noqa: E402
from QueryMind.core.tool import (  # noqa: E402
    ToolCall,
    ToolContext,
    ToolRejection,
    ToolSchema,
)
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
        "grain_keys": ["sales.salesterritory.territoryid"],
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


def test_query_plan_mode_preserves_legacy_switch_and_adaptive_alias() -> None:
    assert parse_query_plan_mode(None) == QueryPlanMode.DISABLED
    assert (
        parse_query_plan_mode(None, require_query_plan=True)
        == QueryPlanMode.ALWAYS
    )
    assert parse_query_plan_mode("risk-based") == QueryPlanMode.ADAPTIVE


def test_adaptive_risk_allows_narrow_one_table_sql() -> None:
    metadata = {
        "schema_evidence": {
            "tables": ["warehouse.sales.orders"],
        }
    }
    simple = assess_query_plan_risk(
        """
        SELECT status, COUNT(*) AS order_count
        FROM sales.orders
        WHERE created_at >= DATE '2026-01-01'
        GROUP BY status
        ORDER BY order_count DESC
        """,
        metadata,
        dialect="postgres",
    )

    assert simple.requires_plan is False
    assert simple.risk_level == "low"


def test_adaptive_risk_requires_plan_for_generic_complex_shapes() -> None:
    metadata = {
        "schema_evidence": {
            "tables": ["warehouse.sales.orders", "warehouse.sales.customers"],
        }
    }
    complex_query = assess_query_plan_risk(
        """
        SELECT c.segment, COUNT(DISTINCT o.order_id) AS order_count
        FROM sales.orders AS o
        JOIN sales.customers AS c ON c.customer_id = o.customer_id
        GROUP BY c.segment
        HAVING COUNT(DISTINCT o.order_id) > 5
        """,
        metadata,
        dialect="postgres",
    )

    assert complex_query.requires_plan is True
    assert {"join", "distinct", "having"} <= set(complex_query.reasons)


def test_adaptive_risk_detects_time_series_functions_case_insensitively() -> None:
    metadata = {
        "schema_evidence": {
            "tables": ["warehouse.sales.orders"],
        }
    }

    monthly = assess_query_plan_risk(
        """
        SELECT DATE_TRUNC('month', order_date), SUM(total)
        FROM sales.orders
        GROUP BY DATE_TRUNC('month', order_date)
        """,
        metadata,
        dialect="postgres",
    )

    assert monthly.requires_plan is True
    assert "time_series" in monthly.reasons


def test_empty_schema_query_is_backfilled_from_user_question() -> None:
    tool_call = ToolCall(
        id="schema-1",
        name="schema_retrieve",
        arguments={"query": "", "table_names": []},
    )

    changed = _backfill_schema_retrieve_query(
        tool_call,
        "按区域统计订单金额",
    )

    assert changed is True
    assert tool_call.arguments["query"] == "按区域统计订单金额"


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


def test_query_plan_requires_stable_primary_key_for_grouped_entity_grain() -> None:
    metadata = {
        "schema_evidence": {
            "table_primary_keys": {
                "chinook.public.track": ["track_id"],
                "chinook.public.invoice_line": ["invoice_line_id"],
            }
        }
    }
    unsafe = QueryPlan(
        objective="Top tracks by quantity sold",
        source_tables=["public.track", "public.invoice_line"],
        required_columns=[
            "public.track.track_id",
            "public.track.name",
            "public.invoice_line.track_id",
            "public.invoice_line.quantity",
        ],
        metric_expressions=["SUM(invoice_line.quantity)"],
        dimensions=["track.name"],
        row_grain="one row per track",
        output_columns=["track_name", "quantity_sold"],
        join_path=["invoice_line.track_id = track.track_id"],
        requires_aggregation=True,
        requires_grouping=True,
    )

    rejected = validate_query_plan_intent(unsafe, metadata, "Top tracks")
    accepted = validate_query_plan_intent(
        unsafe.model_copy(
            update={"grain_keys": ["public.track.track_id"]}
        ),
        metadata,
        "Top tracks",
    )

    assert rejected.issues == [
        "grouped_entity_primary_key_missing_from_grain_keys:"
        "chinook.public.track.track_id"
    ]
    assert accepted.passed is True


def test_query_plan_requires_distinct_for_joined_entity_primary_key_count() -> None:
    metadata = {
        "schema_evidence": {
            "table_primary_keys": {
                "chinook.public.track": ["track_id"],
                "chinook.public.invoice_line": ["invoice_line_id"],
            }
        }
    }
    unsafe = QueryPlan(
        objective="Count catalog and sold tracks by media type",
        source_tables=["public.track", "public.invoice_line"],
        required_columns=[
            "public.track.track_id",
            "public.invoice_line.track_id",
        ],
        metric_expressions=["COUNT(track.track_id) AS catalog_track_count"],
        dimensions=["track.media_type_id"],
        row_grain="one row per media type",
        output_columns=["media_type_id", "catalog_track_count"],
        join_path=["invoice_line.track_id = track.track_id"],
        requires_aggregation=True,
        requires_grouping=True,
    )

    rejected = validate_query_plan_intent(unsafe, metadata, "Count tracks")
    accepted = validate_query_plan_intent(
        unsafe.model_copy(
            update={
                "metric_expressions": [
                    "COUNT(DISTINCT track.track_id) AS catalog_track_count"
                ]
            }
        ),
        metadata,
        "Count tracks",
    )

    assert rejected.issues == [
        "joined_primary_key_count_requires_distinct:track.track_id"
    ]
    assert accepted.passed is True


def test_semantic_contract_does_not_waive_joined_primary_key_distinct_safety() -> None:
    metadata = {
        "schema_evidence": {
            "table_primary_keys": {
                "public.invoice": ["invoice_id"],
                "public.customer": ["customer_id"],
            }
        },
        "semantic_contracts": {
            "metrics": [
                {
                    "id": "sales.invoice_count",
                    "accepted_expressions": ["COUNT(invoice_id)"],
                }
            ]
        },
    }
    plan = QueryPlan(
        objective="Count invoices per customer",
        source_tables=["public.customer", "public.invoice"],
        required_columns=["public.customer.customer_id", "public.invoice.invoice_id"],
        metric_expressions=["COUNT(invoice.invoice_id) AS invoice_count"],
        dimensions=["public.customer.customer_id"],
        grain_keys=["public.customer.customer_id"],
        row_grain="one row per customer",
        output_columns=["customer_id", "invoice_count"],
        join_path=["customer.customer_id = invoice.customer_id"],
        requires_aggregation=True,
        requires_grouping=True,
        semantic_contract_ids=["sales.invoice_count"],
    )

    check = validate_query_plan_intent(plan, metadata, "Count invoices")

    assert check.issues == [
        "joined_primary_key_count_requires_distinct:invoice.invoice_id"
    ]


def test_query_plan_rejects_detail_primary_key_from_final_grain() -> None:
    metadata = {
        "schema_evidence": {
            "table_primary_keys": {"public.track": ["track_id"]}
        }
    }
    plan = QueryPlan(
        objective="Count tracks by composer",
        source_tables=["public.track"],
        required_columns=["public.track.composer", "public.track.track_id"],
        metric_expressions=["COUNT(track_id)"],
        dimensions=["public.track.composer"],
        grain_keys=["public.track.composer", "public.track.track_id"],
        row_grain="one row per composer",
        output_columns=["composer", "track_count"],
        requires_aggregation=True,
        requires_grouping=True,
    )

    check = validate_query_plan_intent(plan, metadata, "Count tracks by composer")

    assert check.issues == [
        "grain_key_conflicts_with_row_grain:public.track.track_id"
    ]


def test_query_plan_requires_partition_limit_for_top_per_group_request() -> None:
    plan = QueryPlan(
        objective="Highest invoice in each country",
        source_tables=["public.invoice"],
        required_columns=[
            "public.invoice.billing_country",
            "public.invoice.invoice_id",
            "public.invoice.total",
        ],
        row_grain="one row per billing country",
        output_columns=["billing_country", "invoice_id", "total", "rank"],
        requires_ordering=True,
    )

    rejected = validate_query_plan_intent(
        plan,
        {},
        "每个账单国家或地区金额最高的一笔订单是什么？",
    )
    accepted = validate_query_plan_intent(
        plan.model_copy(update={"partition_limit": 1}),
        {},
        "每个账单国家或地区金额最高的一笔订单是什么？",
    )

    assert rejected.issues == [
        "top_per_group_limit_mismatch:expected=1:planned=none"
    ]
    assert accepted.passed is True


def test_sql_alignment_requires_planned_grain_key_in_group_by() -> None:
    plan = _sales_plan()
    missing_key = validate_sql_against_query_plan(
        plan,
        """
        SELECT st.name AS territory_name, SUM(soh.totaldue) AS total_sales
        FROM sales.salesorderheader AS soh
        JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
        GROUP BY st.name
        ORDER BY total_sales DESC
        """,
        dialect="postgres",
    )
    aligned = validate_sql_against_query_plan(
        plan,
        """
        SELECT st.name AS territory_name, SUM(soh.totaldue) AS total_sales
        FROM sales.salesorderheader AS soh
        JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
        GROUP BY st.territoryid, st.name
        ORDER BY total_sales DESC
        """,
        dialect="postgres",
    )

    assert missing_key.issues == [
        "planned_grain_key_missing_from_group_by:"
        "sales.salesterritory.territoryid"
    ]
    assert aligned.passed is True


def test_sql_alignment_supports_expression_grain_key() -> None:
    plan = QueryPlan(
        objective="Yearly employee sales",
        source_tables=["public.invoice"],
        required_columns=["public.invoice.invoice_date", "public.invoice.total"],
        metric_expressions=["SUM(invoice.total)"],
        dimensions=["EXTRACT(YEAR FROM public.invoice.invoice_date)"],
        grain_keys=["EXTRACT(YEAR FROM public.invoice.invoice_date)"],
        row_grain="one row per year",
        output_columns=["sales_year", "sales_total"],
        requires_aggregation=True,
        requires_grouping=True,
    )
    sql = """
        SELECT EXTRACT(YEAR FROM i.invoice_date) AS sales_year,
               SUM(i.total) AS sales_total
        FROM public.invoice AS i
        GROUP BY EXTRACT(YEAR FROM i.invoice_date)
    """

    check = validate_sql_against_query_plan(plan, sql, dialect="postgres")

    assert check.passed is True


def test_sql_alignment_recognizes_columns_inside_aggregate_filter() -> None:
    plan = QueryPlan(
        objective="Keep customers with at least seven invoices",
        source_tables=["public.customer", "public.invoice"],
        required_columns=["public.customer.customer_id", "public.invoice.invoice_id"],
        metric_expressions=["COUNT(invoice.invoice_id)"],
        dimensions=["public.customer.customer_id"],
        grain_keys=["public.customer.customer_id"],
        filters=[
            QueryPlanFilter(
                column="COUNT(invoice.invoice_id)",
                operator=">=",
                value_description="7",
            )
        ],
        row_grain="one row per customer",
        output_columns=["customer_id", "invoice_count"],
        join_path=["customer.customer_id = invoice.customer_id"],
        requires_aggregation=True,
        requires_grouping=True,
    )
    check = validate_sql_against_query_plan(
        plan,
        """
        SELECT c.customer_id, COUNT(i.invoice_id) AS invoice_count
        FROM public.customer c
        JOIN public.invoice i ON i.customer_id = c.customer_id
        GROUP BY c.customer_id
        HAVING COUNT(i.invoice_id) >= 7
        """,
        dialect="postgres",
    )

    assert check.passed


def test_sql_alignment_recognizes_rhs_column_in_filter_description() -> None:
    plan = QueryPlan(
        objective="Territories below last year",
        source_tables=["sales.salesterritory"],
        required_columns=[
            "sales.salesterritory.name",
            "sales.salesterritory.salesytd",
            "sales.salesterritory.saleslastyear",
        ],
        filters=[
            QueryPlanFilter(
                column="sales.salesterritory.salesytd",
                operator="<",
                value_description="saleslastyear",
            )
        ],
        row_grain="one row per territory",
        output_columns=["name", "salesytd", "saleslastyear"],
    )
    check = validate_sql_against_query_plan(
        plan,
        """
        SELECT name, salesytd, saleslastyear
        FROM sales.salesterritory
        WHERE salesytd < saleslastyear
        """,
        dialect="postgres",
    )

    assert check.passed


def test_sql_alignment_requires_window_and_filter_for_partition_limit() -> None:
    plan = QueryPlan(
        objective="Highest invoice in each country",
        source_tables=["public.invoice"],
        required_columns=[
            "public.invoice.billing_country",
            "public.invoice.invoice_id",
            "public.invoice.total",
        ],
        row_grain="one row per billing country",
        output_columns=["billing_country", "invoice_id", "total", "rank"],
        requires_ordering=True,
        partition_limit=1,
    )
    unsafe = validate_sql_against_query_plan(
        plan,
        """
        SELECT i.billing_country, i.invoice_id, i.total, 1 AS rank
        FROM public.invoice AS i
        JOIN (
          SELECT billing_country, MAX(total) AS max_total
          FROM public.invoice GROUP BY billing_country
        ) AS m ON m.billing_country = i.billing_country AND m.max_total = i.total
        ORDER BY i.billing_country
        """,
        dialect="postgres",
    )
    safe = validate_sql_against_query_plan(
        plan,
        """
        SELECT billing_country, invoice_id, total, country_rank
        FROM (
          SELECT billing_country, invoice_id, total,
                 ROW_NUMBER() OVER (
                   PARTITION BY billing_country ORDER BY total DESC, invoice_id
                 ) AS country_rank
          FROM public.invoice
        ) AS ranked
        WHERE country_rank = 1
        ORDER BY billing_country
        """,
        dialect="postgres",
    )

    assert "planned_partition_limit_window_missing_from_sql" in unsafe.issues
    assert "planned_partition_limit_filter_missing_from_sql" in unsafe.issues
    assert safe.passed is True


def test_sql_alignment_rejects_rank_for_exact_partition_limit() -> None:
    plan = QueryPlan(
        objective="Top three tracks per genre",
        source_tables=["public.track"],
        required_columns=["public.track.genre_id", "public.track.track_id"],
        row_grain="one row per selected track",
        output_columns=["genre_id", "track_id", "rank"],
        requires_ordering=True,
        partition_limit=3,
    )
    check = validate_sql_against_query_plan(
        plan,
        """
        SELECT genre_id, track_id, genre_rank
        FROM (
          SELECT genre_id, track_id,
                 RANK() OVER (PARTITION BY genre_id ORDER BY unit_price DESC) AS genre_rank
          FROM public.track
        ) ranked
        WHERE genre_rank <= 3
        ORDER BY genre_id, genre_rank
        """,
        dialect="postgres",
    )

    assert "planned_partition_limit_requires_row_number" in check.issues


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


def test_submit_query_plan_returns_actionable_join_count_repair() -> None:
    context = _context(
        {
            "schema_evidence": {
                "tables": ["public.track", "public.invoice_line"],
                "columns": [
                    "public.track.track_id",
                    "public.invoice_line.track_id",
                ],
                "table_primary_keys": {
                    "public.track": ["track_id"],
                    "public.invoice_line": ["invoice_line_id"],
                },
            }
        }
    )
    plan = QueryPlan(
        objective="Count catalog tracks",
        source_tables=["public.track", "public.invoice_line"],
        required_columns=[
            "public.track.track_id",
            "public.invoice_line.track_id",
        ],
        metric_expressions=["COUNT(track.track_id)"],
        dimensions=["track.media_type_id"],
        row_grain="one row per media type",
        output_columns=["media_type_id", "track_count"],
        join_path=["invoice_line.track_id = track.track_id"],
        requires_aggregation=True,
        requires_grouping=True,
    )

    result = asyncio.run(SubmitQueryPlanTool().execute(context, plan))

    assert result.success is False
    assert result.metadata["query_plan_repair_hints"] == [
        "replace COUNT(track.track_id) with "
        "COUNT(DISTINCT track.track_id)"
    ]
    assert "Do not resubmit an unchanged plan" in result.result_for_llm


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
        GROUP BY st.territoryid, st.name
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


def test_sql_alignment_rejects_unplanned_business_filter() -> None:
    plan = _sales_plan()
    sql = """
        SELECT st.name AS territory_name, SUM(soh.totaldue) AS total_sales
        FROM sales.salesorderheader AS soh
        JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
        WHERE st.name = 'Northwest'
        GROUP BY st.territoryid, st.name
        ORDER BY total_sales DESC
    """

    check = validate_sql_against_query_plan(plan, sql, dialect="postgres")

    assert "unplanned_filter_column_in_sql:name" in check.issues


def test_sql_alignment_allows_count_star_to_cover_single_table_support_column() -> None:
    plan = QueryPlan(
        objective="Count tracks by composer",
        source_tables=["chinook.public.track"],
        required_columns=[
            "chinook.public.track.composer",
            "chinook.public.track.track_id",
        ],
        metric_expressions=["COUNT(*) AS work_count"],
        dimensions=["chinook.public.track.composer"],
        grain_keys=["chinook.public.track.composer"],
        filters=[
            QueryPlanFilter(
                column="chinook.public.track.composer",
                operator="IS NOT NULL",
                value_description="ignore missing composers",
            ),
            QueryPlanFilter(
                column="chinook.public.track.composer",
                operator="<> ''",
                value_description="ignore empty composers",
            ),
        ],
        row_grain="one row per composer",
        output_columns=["composer", "work_count"],
        requires_aggregation=True,
        requires_grouping=True,
        requires_ordering=True,
        limit=10,
    )
    sql = """
        SELECT composer, COUNT(*) AS work_count
        FROM chinook.public.track
        WHERE composer IS NOT NULL AND composer <> ''
        GROUP BY composer
        HAVING COUNT(*) >= 5
        ORDER BY work_count DESC, composer
        LIMIT 10
    """

    check = validate_sql_against_query_plan(plan, sql, dialect="postgres")

    assert check.passed is True


def test_sql_alignment_preserves_planned_distinct_entity_count() -> None:
    plan = QueryPlan(
        objective="Count catalog tracks by media type without join fan-out",
        source_tables=[
            "public.media_type",
            "public.track",
            "public.invoice_line",
        ],
        required_columns=[
            "public.media_type.media_type_id",
            "public.track.track_id",
            "public.invoice_line.track_id",
        ],
        metric_expressions=[
            "COUNT(DISTINCT track.track_id) AS catalog_track_count",
            "COUNT(DISTINCT invoice_line.track_id) AS sold_track_count",
        ],
        dimensions=["media_type.media_type_id"],
        row_grain="one row per media type",
        grain_keys=["public.media_type.media_type_id"],
        output_columns=[
            "media_type_id",
            "catalog_track_count",
            "sold_track_count",
        ],
        join_path=[
            "track.media_type_id = media_type.media_type_id",
            "invoice_line.track_id = track.track_id",
        ],
        requires_aggregation=True,
        requires_grouping=True,
    )
    unsafe_sql = """
        SELECT mt.media_type_id,
               COUNT(t.track_id) AS catalog_track_count,
               COUNT(DISTINCT il.track_id) AS sold_track_count
        FROM public.media_type AS mt
        LEFT JOIN public.track AS t ON t.media_type_id = mt.media_type_id
        LEFT JOIN public.invoice_line AS il ON il.track_id = t.track_id
        GROUP BY mt.media_type_id
    """
    safe_sql = unsafe_sql.replace(
        "COUNT(t.track_id)",
        "COUNT(DISTINCT t.track_id)",
    )

    unsafe = validate_sql_against_query_plan(plan, unsafe_sql, dialect="postgres")
    safe = validate_sql_against_query_plan(plan, safe_sql, dialect="postgres")
    aliased_plan = plan.model_copy(
        update={
            "metric_expressions": [
                "COUNT(DISTINCT t.track_id) AS catalog_track_count",
                "COUNT(DISTINCT il.track_id) AS sold_track_count",
            ]
        }
    )
    aliased_safe = validate_sql_against_query_plan(
        aliased_plan,
        safe_sql,
        dialect="postgres",
    )

    assert "planned_distinct_count_missing_from_sql:track.track_id" in unsafe.issues
    assert safe.passed is True
    assert aliased_safe.passed is True


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


def test_query_plan_accepts_aggregate_filter_in_having() -> None:
    plan = _sales_plan(
        filters=[
            QueryPlanFilter(
                column="COUNT(sales.salesorderheader.salesorderid)",
                operator=">=",
                value_description="at least five orders",
            )
        ]
    )
    sql = """
        SELECT customerid, COUNT(salesorderid) AS order_count
        FROM sales.salesorderheader
        GROUP BY customerid
        HAVING COUNT(salesorderid) >= 5
    """

    check = validate_sql_against_query_plan(plan, sql, dialect="postgres")

    assert not any(
        issue.startswith("planned_filter_missing_from_sql")
        for issue in check.issues
    )


def test_adaptive_registry_bypasses_only_low_risk_sql() -> None:
    registry = RLSToolRegistry(query_plan_mode=QueryPlanMode.ADAPTIVE)
    tool = SimpleNamespace(name="run_sql")
    context = _context(
        {
            "dialect": "postgres",
            "schema_evidence": {
                "tables": ["warehouse.production.product"],
            },
        }
    )
    low_risk = RunSqlToolArgs(
        sql="SELECT color, COUNT(*) FROM production.product GROUP BY color"
    )
    high_risk = RunSqlToolArgs(
        sql=(
            "SELECT p.name, SUM(d.quantity) "
            "FROM production.product AS p "
            "JOIN sales.detail AS d ON d.product_id = p.product_id "
            "GROUP BY p.name"
        )
    )

    allowed = asyncio.run(
        registry.transform_args(tool, low_risk, context.user, context)
    )
    allowed_route = context.metadata["query_plan_routing"]["route"]
    rejected = asyncio.run(
        registry.transform_args(tool, high_risk, context.user, context)
    )

    assert isinstance(allowed, RunSqlToolArgs)
    assert allowed_route == "fast"
    assert context.metadata["query_plan_routing"]["route"] == "plan"
    assert isinstance(rejected, ToolRejection)
    assert rejected.code == "adaptive_query_plan_required"


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
