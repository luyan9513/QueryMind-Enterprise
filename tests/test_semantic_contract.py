from __future__ import annotations

import asyncio
from types import SimpleNamespace

from QueryMind.capabilities.sql_runner import RunSqlToolArgs
from QueryMind.core.agent.query_plan import QueryPlan
from QueryMind.core.agent.semantic_contract import (
    SemanticContractCatalog,
    SemanticContractMode,
    SemanticMetricContract,
    load_semantic_contract_catalog,
    parse_semantic_contract_mode,
    validate_query_plan_semantic_contracts,
    validate_sql_against_semantic_contracts,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory
from QueryMind.core.tool import ToolContext, ToolRejection
from QueryMind.core.user import User
from QueryMind.rls_registry import RLSToolRegistry
from QueryMind.tools.schema_retrieve import SchemaRetrieveTool, SchemaRetrieveToolArgs


def _metric(**overrides) -> SemanticMetricContract:
    values = {
        "id": "purchasing.purchase_total",
        "name": "采购订单总金额",
        "aliases": ["采购总额"],
        "description": "Subtotal, tax, and freight combined.",
        "owner": "finance",
        "source_tables": ["purchasing.purchaseorderheader"],
        "required_columns": [
            "purchasing.purchaseorderheader.subtotal",
            "purchasing.purchaseorderheader.taxamt",
            "purchasing.purchaseorderheader.freight",
        ],
        "accepted_expressions": ["SUM(subtotal + taxamt + freight)"],
        "output_alias": "purchase_total",
        "accepted_output_aliases": ["采购总金额"],
        "base_grain": "one purchase order",
    }
    values.update(overrides)
    return SemanticMetricContract(**values)


def _catalog(*metrics: SemanticMetricContract) -> SemanticContractCatalog:
    return SemanticContractCatalog(
        data_source_id="demo",
        version="1.0.0",
        metrics=list(metrics or [_metric()]),
    )


def _snapshot() -> dict:
    return {
        "semantic_contracts": _catalog().build_runtime_snapshot("采购总额")
    }


def _plan(**overrides) -> QueryPlan:
    values = {
        "objective": "Purchase total by vendor",
        "source_tables": ["purchasing.purchaseorderheader"],
        "required_columns": [
            "purchasing.purchaseorderheader.subtotal",
            "purchasing.purchaseorderheader.taxamt",
            "purchasing.purchaseorderheader.freight",
        ],
        "metric_expressions": ["SUM(poh.subtotal + poh.taxamt + poh.freight)"],
        "row_grain": "one row per vendor",
        "output_columns": ["vendor_name", "purchase_total"],
        "requires_aggregation": True,
        "requires_grouping": True,
        "semantic_contract_ids": ["purchasing.purchase_total"],
        "semantic_contract_version": "1.0.0",
    }
    values.update(overrides)
    return QueryPlan(**values)


def test_catalog_loads_and_prefers_specific_alias(tmp_path) -> None:
    path = tmp_path / "contracts.yaml"
    path.write_text(
        """
data_source_id: demo
version: 1.0.0
metrics:
  - id: sales.order_total
    name: 订单总金额
    aliases: []
    description: Sales order total.
    owner: sales
    source_tables: [sales.orders]
    required_columns: [sales.orders.total]
    accepted_expressions: [SUM(total)]
    base_grain: one order
  - id: purchasing.purchase_total
    name: 采购订单总金额
    aliases: []
    description: Purchase order total.
    owner: purchasing
    source_tables: [purchasing.orders]
    required_columns: [purchasing.orders.total]
    accepted_expressions: [SUM(total)]
    base_grain: one purchase order
""".strip(),
        encoding="utf-8",
    )
    catalog = load_semantic_contract_catalog(path)

    assert [item.id for item in catalog.match("各供应商的采购订单总金额")] == [
        "purchasing.purchase_total"
    ]
    assert len(catalog.fingerprint()) == 64
    assert parse_semantic_contract_mode("observe") == SemanticContractMode.ADVISORY


def test_required_plan_must_cite_matched_contract_and_exact_version() -> None:
    missing = validate_query_plan_semantic_contracts(
        _plan(semantic_contract_ids=[]),
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert "semantic_contract_not_cited" in missing.issues

    stale = validate_query_plan_semantic_contracts(
        _plan(semantic_contract_version="0.9.0"),
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert "semantic_contract_version_mismatch" in stale.issues

    accepted = validate_query_plan_semantic_contracts(
        _plan(),
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert accepted.passed


def test_sql_contract_rejects_incomplete_formula_and_advises_on_alias() -> None:
    incomplete = validate_sql_against_semantic_contracts(
        _plan(),
        """
        SELECT vendorid, SUM(subtotal) AS purchase_total
        FROM purchasing.purchaseorderheader
        GROUP BY vendorid
        """,
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert "contract_expression_missing_from_sql:purchasing.purchase_total" in (
        incomplete.issues
    )
    assert any("taxamt" in issue for issue in incomplete.issues)

    wrong_alias = validate_sql_against_semantic_contracts(
        _plan(),
        """
        SELECT vendorid, SUM(subtotal + taxamt + freight) AS total
        FROM purchasing.purchaseorderheader
        GROUP BY vendorid
        """,
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert wrong_alias.passed
    assert (
        "contract_output_alias_differs_from_preference:purchasing.purchase_total"
        in wrong_alias.advisories
    )

    accepted = validate_sql_against_semantic_contracts(
        _plan(),
        """
        SELECT vendorid, SUM(poh.subtotal + poh.taxamt + poh.freight) AS purchase_total
        FROM purchasing.purchaseorderheader AS poh
        GROUP BY vendorid
        """,
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert accepted.passed

    localized_alias = validate_sql_against_semantic_contracts(
        _plan(output_columns=["vendor_name", "采购订单总金额"]),
        """
        SELECT vendorid, SUM(subtotal + taxamt + freight) AS "采购订单总金额"
        FROM purchasing.purchaseorderheader
        GROUP BY vendorid
        """,
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert localized_alias.passed


def test_required_mode_blocks_aggregate_sql_without_a_plan() -> None:
    check = validate_sql_against_semantic_contracts(
        None,
        "SELECT SUM(subtotal + taxamt + freight) FROM purchasing.purchaseorderheader",
        _snapshot(),
        mode="required",
        dialect="postgres",
    )
    assert check.issues == ["semantic_contract_plan_required"]


def test_required_mode_allows_unmatched_aggregate_to_use_normal_governance() -> None:
    check = validate_sql_against_semantic_contracts(
        None,
        "SELECT COUNT(*) FROM sales.orders",
        {},
        mode="required",
        dialect="postgres",
    )
    assert check.passed


def test_registry_blocks_sql_that_does_not_match_cited_contract() -> None:
    context = ToolContext(
        user=User(
            id="u1",
            username="u1",
            email="u1@example.com",
            group_memberships=["user"],
        ),
        conversation_id="c1",
        request_id="r1",
        raw_user_message="各供应商采购总额",
        agent_memory=NoOpAgentMemory(),
        metadata={
            "dialect": "postgres",
            "query_plan_status": "accepted",
            "query_plan": _plan().model_dump(mode="json"),
            **_snapshot(),
        },
    )
    result = asyncio.run(
        RLSToolRegistry(
            config_path="missing-rls-config.yaml",
            query_plan_mode="always",
            semantic_contract_mode="required",
        ).transform_args(
            SimpleNamespace(name="run_sql"),
            RunSqlToolArgs(
                sql=(
                    "SELECT vendorid, "
                    "SUM(subtotal) + SUM(taxamt) * 0 + SUM(freight) * 0 "
                    "AS purchase_total "
                    "FROM purchasing.purchaseorderheader GROUP BY vendorid"
                )
            ),
            context.user,
            context,
        )
    )

    assert isinstance(result, ToolRejection)
    assert result.stage == "semantic_contract"
    assert result.code == "semantic_contract_mismatch"


class _EmptySchemaMemory:
    async def search_schema(self, **kwargs):
        return []


def test_schema_retrieve_returns_matching_contract_with_physical_schema() -> None:
    context = ToolContext(
        user=User(
            id="u1",
            username="u1",
            email="u1@example.com",
            group_memberships=["user"],
        ),
        conversation_id="c1",
        request_id="r1",
        raw_user_message="各供应商采购总额",
        agent_memory=NoOpAgentMemory(),
        metadata={"dialect": "postgres"},
    )
    result = asyncio.run(
        SchemaRetrieveTool(
            schema_memory=_EmptySchemaMemory(),  # type: ignore[arg-type]
            semantic_contract_catalog=_catalog(),
        ).execute(
            context,
            SchemaRetrieveToolArgs(query="采购总额"),
        )
    )

    assert result.success
    assert result.metadata["semantic_contracts"]["matched_metric_ids"] == [
        "purchasing.purchase_total"
    ]
    assert "Approved data-source semantic contracts" in result.result_for_llm
