from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.core.evaluation import (  # noqa: E402
    AgentResult,
    EvaluationDataset,
    EvaluationResult,
    ExpectedSqlContract,
    ExpectedSchema,
    ResultComparisonPolicy,
    SqlExecutionArtifact,
    SqlTestCase,
    ToolInvocationRecord,
)
from QueryMind.core.evaluation.failure_attribution import classify_failure  # noqa: E402
from QueryMind.core.evaluation.metrics import (  # noqa: E402
    calculate_schema_recall,
    compare_execution_artifacts,
    enrich_result_metrics,
    evaluate_sql_contract,
    estimate_usage_cost_usd,
    fingerprint_dataframe,
)
from QueryMind.core.evaluation.sanitization import (  # noqa: E402
    redact_sensitive_text,
    sanitize_export_payload,
    sanitize_trace_metadata,
)
from evals.rescore import rescore_result  # noqa: E402


def _test_case() -> SqlTestCase:
    return SqlTestCase(
        id="enterprise-1",
        database_id="adventureworks",
        dialect="postgres",
        query="各区域销售额",
        ground_truth_sql="SELECT 1",
        expected_schema=ExpectedSchema(
            tables=["sales.salesorderheader", "sales.salesterritory"]
        ),
    )


def _agent_result(tool_calls: list[ToolInvocationRecord] | None = None) -> AgentResult:
    return AgentResult(
        test_case_id="enterprise-1",
        database_id="adventureworks",
        conversation_id="eval-enterprise-1",
        user_id="eval-user",
        tool_calls=tool_calls or [],
    )


def test_dataframe_fingerprint_supports_ordered_and_unordered_comparison() -> None:
    first = fingerprint_dataframe(pd.DataFrame([{"id": 1}, {"id": 2}]))
    reversed_rows = fingerprint_dataframe(pd.DataFrame([{"id": 2}, {"id": 1}]))

    assert first["ordered_result_fingerprint"] != reversed_rows["ordered_result_fingerprint"]
    assert first["unordered_result_fingerprint"] == reversed_rows["unordered_result_fingerprint"]


def test_execution_artifact_comparison_checks_full_result_hash() -> None:
    fingerprints = fingerprint_dataframe(pd.DataFrame([{"id": 1}, {"id": 2}]))
    artifact = SqlExecutionArtifact(
        sql_text="SELECT id FROM demo ORDER BY id",
        success=True,
        row_count=2,
        column_names=["id"],
        **fingerprints,
    )

    comparison = compare_execution_artifacts(
        artifact,
        artifact.model_copy(deep=True),
        order_sensitive=True,
    )

    assert comparison["result_correct"] is True
    assert comparison["column_names_match"] is True


def test_business_comparison_policy_normalizes_precision_and_value_aliases() -> None:
    policy = ResultComparisonPolicy(
        numeric_decimal_places=2,
        value_aliases={"Online": ["线上订单", "online"]},
    )
    agent = fingerprint_dataframe(
        pd.DataFrame([{"channel": "线上订单", "amount": 1.234}]),
        numeric_decimal_places=policy.numeric_decimal_places,
        value_aliases=policy.value_aliases,
    )
    ground_truth = fingerprint_dataframe(
        pd.DataFrame([{"channel": "Online", "amount": 1.23}]),
        numeric_decimal_places=policy.numeric_decimal_places,
        value_aliases=policy.value_aliases,
    )

    assert agent["unordered_result_fingerprint"] == ground_truth[
        "unordered_result_fingerprint"
    ]


def test_sql_contract_catches_semantic_drift_without_dataset_runtime_rules() -> None:
    contract = ExpectedSqlContract(
        required_features=["aggregation", "group_by", "where"],
        required_columns=["territoryid", "totaldue"],
        required_filter_columns=["currentflag"],
        required_projection_aliases=["sales_total"],
        min_projection_count=2,
        max_projection_count=2,
    )
    compliant = evaluate_sql_contract(
        """
        SELECT territoryid, SUM(totaldue) AS sales_total
        FROM sales.salesorderheader
        WHERE currentflag = true
        GROUP BY territoryid
        """,
        contract,
        dialect="postgres",
    )
    drifted = evaluate_sql_contract(
        """
        SELECT territoryid, SUM(subtotal) AS sales_total
        FROM sales.salesorderheader
        GROUP BY territoryid
        """,
        contract,
        dialect="postgres",
    )

    assert compliant["sql_contract_passed"] is True
    assert drifted["sql_contract_passed"] is False
    assert "missing_column:totaldue" in drifted["sql_contract_violations"]
    assert "missing_filter_column:currentflag" in drifted["sql_contract_violations"]


def test_rescore_reuses_hashes_without_calling_model_or_database() -> None:
    hashes = fingerprint_dataframe(pd.DataFrame([{"year": 2024, "total": 10}]))
    artifact = SqlExecutionArtifact(
        sql_text=(
            "SELECT EXTRACT(YEAR FROM orderdate) AS year, SUM(totaldue) AS total "
            "FROM sales.orders GROUP BY EXTRACT(YEAR FROM orderdate) ORDER BY year"
        ),
        success=True,
        row_count=1,
        column_names=["year", "total"],
        comparison_ordered_result_fingerprint=hashes["ordered_result_fingerprint"],
        comparison_unordered_result_fingerprint=hashes[
            "unordered_result_fingerprint"
        ],
        **hashes,
    )
    old_case = _test_case().model_copy(
        update={
            "ground_truth_sql": artifact.sql_text,
            "expected_sql_contract": ExpectedSqlContract(
                required_features=["cte"]
            ),
        }
    )
    result = EvaluationResult(
        test_case=old_case,
        agent_result=_agent_result(),
        agent_artifact=artifact,
        ground_truth_artifact=artifact.model_copy(deep=True),
        passed=False,
        score=0.5,
        metadata={
            "result_comparison_policy": old_case.result_comparison.model_dump(
                mode="json"
            ),
            "sql_contract_passed": False,
            "result_correct": True,
        },
    )
    current_case = old_case.model_copy(
        update={
            "expected_sql_contract": ExpectedSqlContract(
                required_features=["aggregation", "group_by", "order_by"]
            )
        }
    )

    rescored = rescore_result(result, current_case)

    assert rescored.passed is True
    assert rescored.metadata["verified_result_correct"] is True
    assert rescored.metadata["rescored_without_model_or_database"] is True


def test_empty_results_require_matching_column_names() -> None:
    empty_hashes = fingerprint_dataframe(pd.DataFrame(columns=["id"]))
    agent = SqlExecutionArtifact(
        sql_text="SELECT id FROM demo WHERE false",
        success=True,
        row_count=0,
        column_names=["id"],
        **empty_hashes,
    )
    ground_truth = agent.model_copy(
        update={"column_names": ["amount"]},
        deep=True,
    )

    comparison = compare_execution_artifacts(
        agent,
        ground_truth,
        order_sensitive=False,
    )

    assert comparison["result_correct"] is False


def test_schema_recall_only_counts_retrieval_before_first_sql() -> None:
    calls = [
        ToolInvocationRecord(
            tool_call_id="retrieve-1",
            tool_name="schema_retrieve",
            metadata={"selected_tables": ["sales.salesorderheader"]},
        ),
        ToolInvocationRecord(tool_call_id="sql-1", tool_name="run_sql"),
        ToolInvocationRecord(
            tool_call_id="retrieve-2",
            tool_name="schema_retrieve",
            metadata={"selected_tables": ["sales.salesterritory"]},
        ),
    ]

    metrics = calculate_schema_recall(_test_case(), _agent_result(calls))

    assert metrics["schema_recall"] == 0.5
    assert metrics["missing_tables"] == ["sales.salesterritory"]

    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(calls),
        metadata={"first_sql_execution_success": True},
    )
    enrich_result_metrics(result)
    assert result.metadata["first_sql_execution_success"] is False


def test_cost_estimate_uses_cache_hit_miss_and_output_tokens() -> None:
    cost = estimate_usage_cost_usd(
        {
            "prompt_tokens": 1_000_000,
            "prompt_cache_hit_tokens": 400_000,
            "prompt_cache_miss_tokens": 600_000,
            "completion_tokens": 100_000,
        },
        {
            "input_cache_hit_usd_per_million": 0.1,
            "input_cache_miss_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
        },
    )

    assert cost == 0.84
    assert estimate_usage_cost_usd(
        {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        {
            "input_cache_hit_usd_per_million": 0.1,
            "input_cache_miss_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
        },
    ) == 1.0


def test_failure_attribution_prioritizes_permission_rejection() -> None:
    agent_result = _agent_result(
        [
            ToolInvocationRecord(
                tool_call_id="sql-1",
                tool_name="run_sql",
                success=False,
                metadata={"rejection_stage": "permission"},
            )
        ]
    )
    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=agent_result,
        passed=False,
        metadata={"schema_recall": 1.0},
    )

    primary, secondary = classify_failure(result)

    assert primary == "permission_failure"
    assert secondary == []

    recovered = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(
            [
                ToolInvocationRecord(
                    tool_call_id="sql-rejected",
                    tool_name="run_sql",
                    metadata={"rejection_stage": "governance"},
                )
            ]
        ),
        passed=True,
        metadata={"result_correct": True, "schema_recall": 1.0},
    )
    assert classify_failure(recovered) == ("success", ["governance_rejection"])


def test_failure_attribution_recognizes_judge_provider_failure() -> None:
    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(),
        passed=False,
        issue_tags=["judge_request_failed"],
    )

    primary, _ = classify_failure(result)

    assert primary == "provider_failure"


def test_failure_attribution_prioritizes_query_contract_failure() -> None:
    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(),
        agent_artifact=SqlExecutionArtifact(sql_text="SELECT 1", success=True),
        passed=True,
        metadata={
            "result_correct": False,
            "sql_contract_passed": False,
            "schema_recall": 1.0,
        },
    )

    primary, secondary = classify_failure(result)

    assert primary == "query_contract_failure"
    assert secondary == ["result_mismatch"]


def test_trace_sanitization_removes_rows_and_credentials() -> None:
    sanitized = sanitize_trace_metadata(
        {
            "results": [{"customer": "Alice"}],
            "api_key": "secret-value",
            "error": "postgresql://user:password@localhost/db",
        }
    )

    assert sanitized["results"] == "<omitted>"
    assert sanitized["api_key"] == "***REDACTED***"
    assert "password" not in sanitized["error"]
    assert "***REDACTED***" in redact_sensitive_text("Bearer abc.def")

    exported = sanitize_export_payload(
        {"preview_rows": [{"name": "Alice"}], "token_usage": {"prompt_tokens": 10}}
    )
    assert exported["preview_rows"] == "<omitted>"
    assert exported["token_usage"] == {"prompt_tokens": 10}

    report_export = sanitize_export_payload(
        {"results": [{"preview_rows": [{"name": "Alice"}]}]}
    )
    assert isinstance(report_export["results"], list)
    assert report_export["results"][0]["preview_rows"] == "<omitted>"


def test_chinese_business_dataset_is_valid_and_has_schema_contracts() -> None:
    dataset_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "evals"
        / "datasets"
        / "adventureworks_business_zh.yaml"
    )

    dataset = EvaluationDataset.from_yaml(dataset_path)

    assert len(dataset) == 24
    assert all(case.metadata["query_language"] == "zh" for case in dataset)
    assert all(case.expected_schema and case.expected_schema.tables for case in dataset)
    assert {case.difficulty for case in dataset} == {"easy", "medium"}
    assert all(
        evaluate_sql_contract(
            case.ground_truth_sql,
            case.expected_sql_contract,
            dialect=case.dialect,
        )["sql_contract_passed"]
        for case in dataset
    )
