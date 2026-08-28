from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.rescore import rescore_result  # noqa: E402
from QueryMind.core.evaluation import (  # noqa: E402
    AgentResult,
    BenchmarkAdmissionProfile,
    BenchmarkQualityThresholds,
    EvaluationDataset,
    EvaluationReport,
    EvaluationResult,
    ExpectedSchema,
    ExpectedSqlContract,
    ResultComparisonPolicy,
    SqlExecutionArtifact,
    SqlTestCase,
    ToolInvocationRecord,
    assess_benchmark_quality,
)
from QueryMind.core.evaluation.failure_attribution import classify_failure  # noqa: E402
from QueryMind.core.evaluation.metrics import (  # noqa: E402
    calculate_schema_recall,
    compare_execution_artifacts,
    enrich_result_metrics,
    estimate_usage_cost_usd,
    evaluate_sql_contract,
    fingerprint_dataframe,
    wilson_score_interval,
)
from QueryMind.core.evaluation.sanitization import (  # noqa: E402
    redact_sensitive_text,
    sanitize_export_payload,
    sanitize_trace_metadata,
)


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


def test_repeated_benchmark_quality_includes_process_trace_gate() -> None:
    profile = BenchmarkAdmissionProfile(
        name="demo",
        database_id="chinook",
        minimum_cases=1,
        required_repeats=3,
        quality_thresholds=BenchmarkQualityThresholds(
            reference_sql_success_rate=1,
            schema_recall=0.9,
            business_accuracy=0.8,
            wrong_executed_rate=0.1,
            automatic_answer_coverage=0.85,
            p95_agent_execution_time_ms=30000,
            repeat_consistency_rate=0.85,
            process_trace_coverage=0.95,
        ),
    )
    case = SqlTestCase(
        id="case-1",
        database_id="chinook",
        dialect="postgres",
        query="统计收入",
        ground_truth_sql="SELECT 1",
    )
    reports = []
    for _ in range(3):
        agent_result = AgentResult(
            test_case_id=case.id,
            database_id=case.database_id,
            conversation_id="conv-1",
            user_id="eval",
            execution_time_ms=100,
            metadata={"process_event_count": 4},
        )
        result = EvaluationResult(
            test_case=case,
            agent_result=agent_result,
            metadata={
                "schema_recall": 1.0,
                "business_result_correct": True,
                "agent_sql_execution_success": True,
            },
        )
        reports.append(
            EvaluationReport(
                dataset_name="demo",
                results=[result],
                evaluator_names=[],
                metadata={
                    "config_snapshot": {
                        "dataset_hash": "dataset-v1",
                        "code_snapshot_id": "code-v1",
                        "database_id": "chinook",
                        "database_snapshot_id": "database-v1",
                        "schema_snapshot_id": "schema-v1",
                        "agent_model": "agent-v1",
                        "agent_provider": "test",
                        "judge_model": "judge-v1",
                        "judge_provider": "test",
                    }
                },
            )
        )

    assessment = assess_benchmark_quality(
        reports,
        profile,
        reference_sql_success_rate=1.0,
    )

    assert assessment.quality_ready is True
    assert assessment.metrics["repeat_consistency_rate"] == 1.0
    assert assessment.metrics["process_trace_coverage"] == 1.0
    assert assessment.comparability_issues == []


def test_repeated_benchmark_quality_fails_closed_when_runs_are_not_comparable() -> None:
    profile = BenchmarkAdmissionProfile(
        name="demo",
        database_id="chinook",
        minimum_cases=1,
        required_repeats=2,
        quality_thresholds=BenchmarkQualityThresholds(
            reference_sql_success_rate=0,
            schema_recall=0,
            business_accuracy=0,
            wrong_executed_rate=1,
            automatic_answer_coverage=0,
            p95_agent_execution_time_ms=30000,
            repeat_consistency_rate=0,
            process_trace_coverage=0,
        ),
    )
    case = SqlTestCase(
        id="case-1",
        database_id="chinook",
        dialect="postgres",
        query="统计收入",
        ground_truth_sql="SELECT 1",
    )
    reports = []
    for model in ("agent-v1", "agent-v2"):
        reports.append(
            EvaluationReport(
                dataset_name="demo",
                results=[
                    EvaluationResult(
                        test_case=case,
                        agent_result=AgentResult(
                            test_case_id=case.id,
                            database_id=case.database_id,
                            conversation_id="conv-1",
                            user_id="eval",
                            execution_time_ms=100,
                        ),
                        metadata={"business_result_correct": True},
                    )
                ],
                metadata={
                    "config_snapshot": {
                        "dataset_hash": "dataset-v1",
                        "code_snapshot_id": "code-v1",
                        "database_id": "chinook",
                        "database_snapshot_id": "database-v1",
                        "schema_snapshot_id": "schema-v1",
                        "agent_model": model,
                        "agent_provider": "test",
                        "judge_model": "judge-v1",
                        "judge_provider": "test",
                    }
                },
            )
        )

    assessment = assess_benchmark_quality(
        reports,
        profile,
        reference_sql_success_rate=1.0,
    )

    assert assessment.quality_ready is False
    assert "agent_model:repeat_1!=repeat_2" in assessment.comparability_issues


def test_repeated_benchmark_quality_aligns_concurrent_results_by_case_id() -> None:
    profile = BenchmarkAdmissionProfile(
        name="ordered-by-id",
        database_id="chinook",
        minimum_cases=2,
        required_repeats=2,
        quality_thresholds=BenchmarkQualityThresholds(
            reference_sql_success_rate=0,
            schema_recall=0,
            business_accuracy=0,
            wrong_executed_rate=1,
            automatic_answer_coverage=0,
            p95_agent_execution_time_ms=30000,
            repeat_consistency_rate=1,
            process_trace_coverage=0,
        ),
    )
    snapshot = {
        "dataset_hash": "dataset-v1",
        "code_snapshot_id": "code-v1",
        "database_id": "chinook",
        "database_snapshot_id": "database-v1",
        "schema_snapshot_id": "schema-v1",
        "agent_model": "agent-v1",
        "agent_provider": "test",
        "judge_model": "judge-v1",
        "judge_provider": "test",
    }

    def make_result(case_id: str, correct: bool) -> EvaluationResult:
        case = SqlTestCase(
            id=case_id,
            database_id="chinook",
            dialect="postgres",
            query="统计收入",
            ground_truth_sql="SELECT 1",
        )
        return EvaluationResult(
            test_case=case,
            agent_result=AgentResult(
                test_case_id=case_id,
                database_id="chinook",
                conversation_id=f"conv-{case_id}",
                user_id="eval",
                execution_time_ms=100,
            ),
            metadata={"business_result_correct": correct},
        )

    first = [make_result("case-a", True), make_result("case-b", False)]
    second = [make_result("case-b", False), make_result("case-a", True)]
    assessment = assess_benchmark_quality(
        [
            EvaluationReport(
                dataset_name="demo",
                results=first,
                metadata={"config_snapshot": snapshot},
            ),
            EvaluationReport(
                dataset_name="demo",
                results=second,
                metadata={"config_snapshot": snapshot},
            ),
        ],
        profile,
        reference_sql_success_rate=1,
    )

    assert assessment.metrics["repeat_consistency_rate"] == 1


def test_repeated_benchmark_quality_builds_case_and_split_failure_ledger() -> None:
    profile = BenchmarkAdmissionProfile(
        name="failure-ledger",
        database_id="chinook",
        minimum_cases=3,
        required_repeats=3,
        quality_thresholds=BenchmarkQualityThresholds(
            reference_sql_success_rate=0,
            schema_recall=0,
            business_accuracy=0,
            wrong_executed_rate=1,
            automatic_answer_coverage=0,
            p95_agent_execution_time_ms=30000,
            repeat_consistency_rate=0,
            process_trace_coverage=0,
        ),
    )
    snapshot = {
        "dataset_hash": "dataset-v1",
        "code_snapshot_id": "code-v1",
        "database_id": "chinook",
        "database_snapshot_id": "database-v1",
        "schema_snapshot_id": "schema-v1",
        "agent_model": "agent-v1",
        "agent_provider": "test",
        "judge_model": "judge-v1",
        "judge_provider": "test",
    }
    outcomes = {
        "case-a": ("development", [False, False, False]),
        "case-b": ("test", [True, False, True]),
        "case-c": ("holdout", [True, True, True]),
    }
    reports = []
    for repeat_index in range(3):
        results = []
        for case_id, (split, values) in outcomes.items():
            test_case = SqlTestCase(
                id=case_id,
                database_id="chinook",
                dialect="postgres",
                query="统计收入",
                ground_truth_sql="SELECT 1",
                metadata={"benchmark_split": split},
            )
            results.append(
                EvaluationResult(
                    test_case=test_case,
                    agent_result=AgentResult(
                        test_case_id=case_id,
                        database_id="chinook",
                        conversation_id=f"conv-{case_id}-{repeat_index}",
                        user_id="eval",
                        execution_time_ms=100,
                    ),
                    metadata={
                        "business_result_correct": values[repeat_index],
                        "agent_sql_execution_success": True,
                    },
                )
            )
        reports.append(
            EvaluationReport(
                dataset_name="demo",
                results=results,
                metadata={"config_snapshot": snapshot},
            )
        )

    assessment = assess_benchmark_quality(
        reports,
        profile,
        reference_sql_success_rate=1,
    )

    assert assessment.metrics["business_accuracy"] == pytest.approx(5 / 9)
    assert assessment.metrics["wrong_executed_rate"] == pytest.approx(4 / 9)
    assert assessment.metrics["repeat_consistency_rate"] == pytest.approx(2 / 3)
    assert assessment.stable_incorrect_case_ids == ["case-a"]
    assert assessment.flaky_case_ids == ["case-b"]
    assert assessment.case_stability[0].model_dump() == {
        "case_id": "case-a",
        "benchmark_split": "development",
        "correct_repeats": 0,
        "total_repeats": 3,
        "status": "stable_incorrect",
    }
    assert assessment.split_metrics["development"].model_dump() == {
        "case_count": 1,
        "sample_count": 3,
        "business_accuracy": 0.0,
        "wrong_executed_rate": 1.0,
        "repeat_consistency_rate": 1.0,
    }
    assert assessment.split_metrics["test"].business_accuracy == pytest.approx(2 / 3)
    assert assessment.split_metrics["test"].wrong_executed_rate == pytest.approx(1 / 3)
    assert assessment.split_metrics["test"].repeat_consistency_rate == 0
    assert assessment.split_metrics["holdout"].business_accuracy == 1
    assert "## Failure stability ledger" in assessment.to_markdown()
    assert "## Metrics by benchmark split" in assessment.to_markdown()


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


def test_dataframe_fingerprint_normalizes_equivalent_numeric_types() -> None:
    decimal_year = fingerprint_dataframe(
        pd.DataFrame([{"year": Decimal("2024.0"), "total": Decimal("10.50")}])
    )
    native_numbers = fingerprint_dataframe(
        pd.DataFrame([{"year": 2024, "total": 10.5}])
    )

    assert decimal_year["ordered_result_fingerprint"] == native_numbers[
        "ordered_result_fingerprint"
    ]


def test_dataframe_fingerprint_can_compare_naive_midnight_at_date_granularity() -> None:
    calendar_date = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": date(2024, 1, 2)}]),
        temporal_granularity="date",
    )
    midnight_timestamp = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": datetime(2024, 1, 2)}]),
        temporal_granularity="date",
    )

    assert calendar_date["ordered_result_fingerprint"] == midnight_timestamp[
        "ordered_result_fingerprint"
    ]


def test_dataframe_fingerprint_keeps_temporal_comparison_exact_by_default() -> None:
    calendar_date = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": date(2024, 1, 2)}])
    )
    midnight_timestamp = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": datetime(2024, 1, 2)}])
    )

    assert calendar_date["ordered_result_fingerprint"] != midnight_timestamp[
        "ordered_result_fingerprint"
    ]


def test_date_granularity_does_not_hide_time_or_timezone_differences() -> None:
    calendar_date = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": date(2024, 1, 2)}]),
        temporal_granularity="date",
    )
    non_midnight = fingerprint_dataframe(
        pd.DataFrame([{"invoice_date": datetime(2024, 1, 2, 8, 30)}]),
        temporal_granularity="date",
    )
    timezone_aware = fingerprint_dataframe(
        pd.DataFrame(
            [{"invoice_date": datetime(2024, 1, 2, tzinfo=timezone.utc)}]
        ),
        temporal_granularity="date",
    )

    assert calendar_date["ordered_result_fingerprint"] != non_midnight[
        "ordered_result_fingerprint"
    ]
    assert calendar_date["ordered_result_fingerprint"] != timezone_aware[
        "ordered_result_fingerprint"
    ]


def test_chinook_date_granularity_is_explicit_and_case_scoped() -> None:
    dataset = EvaluationDataset.from_yaml(
        Path(__file__).resolve().parents[1]
        / "src/evals/datasets/chinook_business_zh.yaml"
    )
    policies = {
        case.id: case.result_comparison.temporal_granularity
        for case in dataset.test_cases
    }

    date_scoped_cases = {
        "ch_zh_018",
        "ch_zh_033",
        "ch_zh_037",
        "ch_zh_067",
        "ch_zh_069",
        "ch_zh_091",
    }
    assert all(policies[case_id] == "date" for case_id in date_scoped_cases)
    assert all(
        granularity == "exact"
        for case_id, granularity in policies.items()
        if case_id not in date_scoped_cases
    )


def test_result_comparison_rejects_unknown_temporal_granularity() -> None:
    with pytest.raises(ValueError):
        ResultComparisonPolicy(temporal_granularity="hour")


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


def test_sql_contract_accepts_any_declared_required_feature_group_member() -> None:
    contract = ExpectedSqlContract(
        required_features=["aggregation", "group_by"],
        required_feature_groups=[["where", "having"]],
    )
    where_result = evaluate_sql_contract(
        "SELECT customer_id, SUM(total) FROM invoice "
        "WHERE total > 0 GROUP BY customer_id",
        contract,
        dialect="postgres",
    )
    having_result = evaluate_sql_contract(
        "SELECT customer_id, SUM(total) FROM invoice "
        "GROUP BY customer_id HAVING SUM(total) > 0",
        contract,
        dialect="postgres",
    )
    missing_result = evaluate_sql_contract(
        "SELECT customer_id, SUM(total) FROM invoice GROUP BY customer_id",
        contract,
        dialect="postgres",
    )

    assert where_result["sql_contract_passed"] is True
    assert having_result["sql_contract_passed"] is True
    assert missing_result["sql_contract_violations"] == [
        "missing_feature_group:having|where"
    ]


def test_sql_contract_can_report_shape_features_as_advisory() -> None:
    result = evaluate_sql_contract(
        "SELECT customer_id, COUNT(*) FROM invoice GROUP BY customer_id",
        ExpectedSqlContract(
            required_features=["cte", "where"],
            feature_requirement_mode="advisory",
        ),
        dialect="postgres",
    )

    assert result["sql_contract_passed"] is True
    assert result["sql_contract_violations"] == []
    assert result["sql_contract_advisories"] == [
        "missing_feature:cte",
        "missing_feature:where",
    ]


def test_chinook_average_customer_spend_contract_allows_where_or_having() -> None:
    dataset = EvaluationDataset.from_yaml(
        Path(__file__).resolve().parents[1]
        / "src/evals/datasets/chinook_business_zh.yaml"
    )
    test_case = next(case for case in dataset.test_cases if case.id == "ch_zh_016")

    assert test_case.expected_sql_contract is not None
    assert test_case.expected_sql_contract.required_feature_groups == [
        ["where", "having"]
    ]
    assert "where" not in test_case.expected_sql_contract.required_features


def test_chinook_v010_frozen_splits_have_valid_reference_contracts() -> None:
    dataset_path = (
        Path(__file__).resolve().parents[1]
        / "src/evals/datasets/chinook_business_zh.yaml"
    )
    dataset = EvaluationDataset.from_yaml(dataset_path)

    assert len(dataset) == 100
    assert {case.id for case in dataset.test_cases} == {
        f"ch_zh_{index:03d}" for index in range(1, 101)
    }
    assert [
        sum(case.metadata.get("benchmark_split") == split for case in dataset)
        for split in ("development", "test", "holdout")
    ] == [60, 20, 20]
    assert all(case.expected_schema and case.expected_schema.tables for case in dataset)
    assert all(
        case.expected_sql_contract is None
        or case.expected_sql_contract.feature_requirement_mode == "advisory"
        for case in dataset
    )
    assert all(
        evaluate_sql_contract(
            case.ground_truth_sql,
            case.expected_sql_contract,
            dialect=case.dialect,
        )["sql_contract_passed"]
        for case in dataset
    )


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
        ToolInvocationRecord(
            tool_call_id="sql-1",
            tool_name="run_sql",
            success=True,
        ),
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
    assert result.metadata["first_sql_execution_success"] is True
    assert result.metadata["agent_sql_execution_success"] is True


def test_schema_recall_continues_after_rejected_sql_attempt() -> None:
    calls = [
        ToolInvocationRecord(
            tool_call_id="sql-rejected",
            tool_name="run_sql",
            success=False,
        ),
        ToolInvocationRecord(
            tool_call_id="retrieve-after-rejection",
            tool_name="schema_retrieve",
            success=True,
            metadata={
                "selected_tables": [
                    "sales.salesorderheader",
                    "sales.salesterritory",
                ]
            },
        ),
    ]

    metrics = calculate_schema_recall(_test_case(), _agent_result(calls))

    assert metrics["schema_recall"] == 1.0


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


def test_failure_attribution_recognizes_semantic_contract_block() -> None:
    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(
            [
                ToolInvocationRecord(
                    tool_call_id="sql-1",
                    tool_name="run_sql",
                    success=False,
                    metadata={"rejection_stage": "semantic_contract"},
                )
            ]
        ),
        passed=False,
        issue_tags=["missing_sql"],
    )

    primary, _ = classify_failure(result)

    assert primary == "semantic_contract_failure"


def test_failure_attribution_does_not_treat_offline_replay_as_agent_success() -> None:
    result = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(
            [
                ToolInvocationRecord(
                    tool_call_id="plan-1",
                    tool_name="submit_query_plan",
                    success=False,
                    metadata={
                        "query_plan_issues": [
                            "contract_output_alias_missing_from_plan:sales.revenue"
                        ]
                    },
                )
            ]
        ),
        passed=False,
        issue_tags=["missing_sql"],
        metadata={
            "result_correct": True,
            "verified_result_correct": False,
        },
    )

    primary, _ = classify_failure(result)

    assert primary == "semantic_contract_failure"


def test_report_pairs_answer_precision_with_contract_coverage() -> None:
    contract_trace = [
        ToolInvocationRecord(
            tool_call_id="schema-1",
            tool_name="schema_retrieve",
            metadata={
                "semantic_contracts": {
                    "matched_metric_ids": ["sales.order_total"]
                }
            },
        ),
        ToolInvocationRecord(
            tool_call_id="plan-1",
            tool_name="submit_query_plan",
            metadata={
                "query_plan": {
                    "semantic_contract_ids": ["sales.order_total"]
                }
            },
        ),
        ToolInvocationRecord(
            tool_call_id="sql-1",
            tool_name="run_sql",
            success=True,
            metadata={
                "semantic_contract_validation": {"passed": True}
            },
        ),
    ]
    answered = EvaluationResult(
        test_case=_test_case(),
        agent_result=_agent_result(contract_trace),
        passed=True,
        metadata={
            "agent_sql_execution_success": True,
            "business_result_correct": True,
        },
    )
    abstained = EvaluationResult(
        test_case=_test_case().model_copy(update={"id": "enterprise-2"}),
        agent_result=_agent_result(),
        passed=False,
        metadata={
            "agent_sql_execution_success": False,
            "business_result_correct": False,
        },
    )
    report = EvaluationReport(dataset_name="demo", results=[answered, abstained])

    assert report.automatic_answer_coverage() == 0.5
    assert report.automatic_answer_precision() == 1.0
    assert report.semantic_contract_metrics() == {
        "matched_cases": 1,
        "cited_cases": 1,
        "validation_passed_attempts": 1,
        "validation_rejected_attempts": 0,
    }


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


def test_wilson_interval_is_bounded_and_keeps_observed_rate() -> None:
    interval = wilson_score_interval(9, 10)

    assert interval["rate"] == 0.9
    assert 0.0 < interval["lower"] < 0.9
    assert 0.9 < interval["upper"] <= 1.0
    assert wilson_score_interval(0, 0)["rate"] is None
