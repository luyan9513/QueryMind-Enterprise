from __future__ import annotations

from evals.audit_benchmark_contracts import audit_case
from QueryMind.core.evaluation import ExpectedSqlContract, ResultComparisonPolicy, SqlTestCase


def _case(**overrides: object) -> SqlTestCase:
    values = {
        "id": "demo-1",
        "database_id": "demo",
        "dialect": "postgres",
        "query": "返回客户编号和金额。",
        "ground_truth_sql": "SELECT customer_id, total FROM invoice ORDER BY total DESC",
        "expected_sql_contract": ExpectedSqlContract(max_projection_count=2),
    }
    values.update(overrides)
    return SqlTestCase(**values)


def test_audit_flags_order_in_reference_but_not_question() -> None:
    result = audit_case(_case())

    assert result.projection_aliases == ["customer_id", "total"]
    assert result.effective_order_sensitive is True
    assert [issue.code for issue in result.issues] == ["implicit_order_requirement"]


def test_audit_respects_explicit_order_insensitive_policy() -> None:
    result = audit_case(
        _case(result_comparison=ResultComparisonPolicy(order_sensitive=False))
    )

    assert result.effective_order_sensitive is False
    assert result.issues == []


def test_audit_flags_reference_projection_contract_mismatch() -> None:
    result = audit_case(
        _case(expected_sql_contract=ExpectedSqlContract(max_projection_count=1))
    )

    assert [issue.code for issue in result.issues] == [
        "reference_above_projection_max",
        "implicit_order_requirement",
    ]


def test_audit_detects_explicit_chinese_order_requirement() -> None:
    result = audit_case(
        _case(query="返回客户编号和金额，按金额从高到低排列。")
    )

    assert result.question_has_order_cue is True
    assert result.issues == []
