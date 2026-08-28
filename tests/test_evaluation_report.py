from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.compare_runs import load_report  # noqa: E402
from QueryMind.core.evaluation import (  # noqa: E402
    AgentResult,
    ComparisonReport,
    EvaluationReport,
    EvaluationResult,
    SqlExecutionArtifact,
    SqlTestCase,
    ToolInvocationRecord,
)


def _make_result(
    *,
    test_case_id: str,
    passed: bool,
    agent_success: bool,
    score: float,
    execution_time_ms: float,
    reported_execution_time_ms: float | None = None,
    difficulty: str = "medium",
    category: str = "analytics",
    source: str = "generated",
    query_language: str = "sql",
) -> EvaluationResult:
    test_case = SqlTestCase(
        id=test_case_id,
        database_id="demo_db",
        dialect="postgres",
        query="SELECT 1",
        ground_truth_sql="SELECT 1",
        difficulty=difficulty,
        metadata={
            "category": category,
            "source": source,
            "query_language": query_language,
        },
    )
    agent_result = AgentResult(
        test_case_id=test_case_id,
        database_id="demo_db",
        conversation_id="conv-1",
        user_id="user-1",
        execution_time_ms=execution_time_ms,
    )
    agent_artifact = SqlExecutionArtifact(
        sql_text="SELECT 1",
        success=agent_success,
        execution_time_ms=execution_time_ms,
    )
    return EvaluationResult(
        test_case=test_case,
        agent_result=agent_result,
        agent_artifact=agent_artifact,
        score=score,
        passed=passed,
        reason="ok" if passed else "failed",
        execution_time_ms=reported_execution_time_ms
        if reported_execution_time_ms is not None
        else execution_time_ms,
    )


def test_save_html_includes_models_filter_and_two_decimal_rates(tmp_path: Path) -> None:
    report = EvaluationReport(
        dataset_name="demo dataset",
        results=[
            _make_result(
                test_case_id="case-1",
                passed=True,
                agent_success=True,
                score=1.0,
                execution_time_ms=120.0,
            ),
            _make_result(
                test_case_id="case-2",
                passed=False,
                agent_success=False,
                score=0.0,
                execution_time_ms=180.0,
            ),
        ],
        evaluator_names=["sql_accuracy"],
        metadata={
            "config_snapshot": {
                "agent_model": "deepseek-v4-flash",
                "judge_model": "deepseek-v4-flash",
            }
        },
    )

    output_path = tmp_path / "evaluation_report.html"
    report.save_html(output_path)
    html = output_path.read_text(encoding="utf-8")

    assert "Agent Model" in html
    assert "Judge Model" in html
    assert "deepseek-v4-flash" in html
    assert "Average Eval Agent Time" in html
    assert 'id="pass-filter"' in html
    assert 'id="sql-execution-filter"' in html
    assert "<th>SQL Execution</th>" in html
    assert "<th>Avg Eval Agent Time (ms)</th>" in html
    assert '<option value="PASS">PASS</option>' in html
    assert '<option value="FAIL">FAIL</option>' in html
    assert '<option value="SUCCESS">SUCCESS</option>' in html
    assert 'data-passed="PASS"' in html
    assert 'data-passed="FAIL"' in html
    assert 'data-sql-execution="SUCCESS"' in html
    assert 'data-sql-execution="FAIL"' in html
    assert "row.dataset.passed === passStatus" in html
    assert "row.dataset.sqlExecution === sqlExecution" in html
    assert "50.00%" in html
    assert "50.0%" not in html


def test_average_execution_time_uses_agent_runtime(tmp_path: Path) -> None:
    report = EvaluationReport(
        dataset_name="demo dataset",
        results=[
            _make_result(
                test_case_id="case-1",
                passed=True,
                agent_success=True,
                score=1.0,
                execution_time_ms=120.0,
                reported_execution_time_ms=999.0,
            )
        ],
        evaluator_names=["sql_accuracy"],
    )

    assert report.average_execution_time() == 120.0

    output_path = tmp_path / "evaluation_report.html"
    report.save_html(output_path)
    html = output_path.read_text(encoding="utf-8")

    assert "Average Eval Agent Time" in html


def test_enterprise_metrics_and_failure_filter_are_exported(tmp_path: Path) -> None:
    result = _make_result(
        test_case_id="case-enterprise",
        passed=False,
        agent_success=True,
        score=0.5,
        execution_time_ms=250.0,
    )
    result.metadata.update(
        {
            "schema_recall": 0.5,
            "result_correct": False,
            "first_sql_execution_success": True,
            "first_sql_result_correct": False,
            "tool_call_count": 3,
            "primary_failure": "schema_recall_failure",
            "secondary_failures": ["result_mismatch"],
        }
    )
    result.agent_result.token_usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
    }
    report = EvaluationReport(
        dataset_name="enterprise metrics",
        results=[result],
        metadata={
            "config_snapshot": {
                "pricing": {
                    "agent": {
                        "input_cache_hit_usd_per_million": 0.1,
                        "input_cache_miss_usd_per_million": 1.0,
                        "output_usd_per_million": 2.0,
                    }
                }
            }
        },
    )

    output_path = tmp_path / "enterprise.html"
    report.save_html(output_path)
    rendered = output_path.read_text(encoding="utf-8")

    assert "Schema Recall" in rendered
    assert "Evaluator Pass Rate" in rendered
    assert "Strict Result Correct Rate" in rendered
    assert "Evaluator Pass / Fail" in rendered
    assert "First SQL Strict Correct" in rendered
    assert "First SQL Correct Rate" in rendered
    assert "Estimated Model Cost" in rendered
    assert 'id="failure-filter"' in rendered
    assert 'data-primary-failure="schema_recall_failure"' in rendered
    assert "row.dataset.primaryFailure === primaryFailure" in rendered
    assert report.metadata["enterprise_metrics"]["failure_distribution"] == {
        "schema_recall_failure": 1
    }
    assert report.metadata["enterprise_metrics"]["secondary_failure_distribution"] == {
        "result_mismatch": 1
    }
    assert "Recovered / Secondary Signals" in rendered


def test_accuracy_uses_all_cases_and_answer_precision_uses_executed_cases() -> None:
    correct = _make_result(
        test_case_id="case-correct",
        passed=True,
        agent_success=True,
        score=1.0,
        execution_time_ms=100.0,
    )
    correct.metadata.update(
        {
            "agent_sql_execution_success": True,
            "verified_result_correct": True,
            "business_result_correct": True,
        }
    )
    abstained = _make_result(
        test_case_id="case-abstained",
        passed=False,
        agent_success=False,
        score=0.0,
        execution_time_ms=100.0,
    )
    report = EvaluationReport(dataset_name="coverage", results=[correct, abstained])

    assert report.result_correct_rate() == 0.5
    assert report.business_result_correct_rate() == 0.5
    assert report.automatic_answer_coverage() == 0.5
    assert report.automatic_answer_precision() == 1.0


def test_detailed_markdown_explains_accuracy_sql_and_failure(tmp_path: Path) -> None:
    result = _make_result(
        test_case_id="case-detail",
        passed=False,
        agent_success=True,
        score=0.5,
        execution_time_ms=1250.0,
    )
    result.test_case.query = "按区域统计销售额"
    result.test_case.ground_truth_sql = "SELECT region, SUM(total) FROM orders GROUP BY region"
    result.agent_artifact.sql_text = "SELECT region, SUM(subtotal) FROM orders GROUP BY region"
    result.metadata.update(
        {
            "verified_result_correct": False,
            "business_result_correct": False,
            "sql_contract_passed": False,
            "sql_contract_violations": ["missing_column:total"],
            "sql_contract_advisories": ["missing_feature:cte"],
            "schema_recall": 1.0,
            "tool_call_count": 2,
            "primary_failure": "query_contract_failure",
        }
    )
    report = EvaluationReport(dataset_name="可读评测", results=[result])

    output_path = tmp_path / "evaluation_detailed.md"
    report.save_detailed_markdown(output_path)
    rendered = output_path.read_text(encoding="utf-8")

    assert "严格结果准确率：0.00%" in rendered
    assert "用户问题：按区域统计销售额" in rendered
    assert "参考 SQL" in rendered
    assert "SUM(total)" in rendered
    assert "Agent 最终 SQL" in rendered
    assert "SUM(subtotal)" in rendered
    assert "哪里错了" in rendered
    assert "缺少必要字段：total" in rendered
    assert "SQL 实现形态 advisory 题数：1" in rendered
    assert "SQL 实现形态提示：缺少必要 SQL 结构：cte" in rendered
    assert "为什么错" in rendered
    assert "建议怎么改" in rendered


def test_agent_value_metrics_and_wilson_intervals_are_exported() -> None:
    recovered = _make_result(
        test_case_id="recovered",
        passed=True,
        agent_success=True,
        score=1.0,
        execution_time_ms=1000.0,
    )
    recovered.metadata.update(
        {
            "first_sql_candidate_business_result_correct": False,
            "first_sql_execution_success": True,
            "business_result_correct": True,
            "verified_result_correct": True,
            "accepted_query_plan": True,
            "agent_sql_execution_success": True,
        }
    )
    recovered.agent_result.tool_calls = [
        ToolInvocationRecord(
            tool_call_id="plan",
            tool_name="submit_query_plan",
            success=True,
        ),
        ToolInvocationRecord(
            tool_call_id="sql",
            tool_name="run_sql",
            success=True,
            metadata={
                "query_plan_routing": {
                    "mode": "adaptive",
                    "route": "fast",
                }
            },
        ),
        ToolInvocationRecord(
            tool_call_id="schema",
            tool_name="schema_retrieve",
            success=True,
            metadata={"schema_query_fallback_used": True},
        ),
    ]
    blocked = _make_result(
        test_case_id="blocked",
        passed=False,
        agent_success=True,
        score=0.0,
        execution_time_ms=3000.0,
    )
    blocked.metadata.update(
        {
            "first_sql_candidate_business_result_correct": True,
            "first_sql_execution_success": False,
            "business_result_correct": False,
            "verified_result_correct": False,
            "agent_sql_execution_success": True,
        }
    )
    report = EvaluationReport(dataset_name="agent value", results=[recovered, blocked])

    report.enrich_metadata()
    metrics = report.metadata["enterprise_metrics"]

    assert metrics["recovery_yield"]["rate"] == 1.0
    assert metrics["false_block_rate"]["rate"] == 1.0
    assert metrics["plan_acceptance_precision"]["rate"] == 1.0
    assert metrics["wrong_executed_rate"] == 0.5
    assert metrics["p50_agent_execution_time_ms"] == 2000.0
    assert metrics["max_agent_execution_time_ms"] == 3000.0
    assert metrics["tool_call_totals"] == {
        "run_sql": 1,
        "schema_retrieve": 1,
        "submit_query_plan": 1,
    }
    assert metrics["query_plan_route_totals"] == {"fast": 1}
    assert metrics["schema_query_fallback_count"] == 1
    assert metrics["accuracy_wilson_95"]["strict_result_correct"]["rate"] == 0.5


def test_comparison_report_rejects_unfair_model_change(tmp_path: Path) -> None:
    s0 = EvaluationReport(
        dataset_name="demo",
        results=[
            _make_result(
                test_case_id="case-1",
                passed=True,
                agent_success=True,
                score=1.0,
                execution_time_ms=100.0,
            )
        ],
        metadata={
            "config_snapshot": {
                "dataset_hash": "same",
                "agent_model": "model-a",
                "evaluation_mode": "s0_single_shot",
            }
        },
    )
    s1 = s0.model_copy(deep=True)
    s1.metadata["config_snapshot"]["agent_model"] = "model-b"
    s1.metadata["config_snapshot"]["evaluation_mode"] = "s1_agent_without_plan"
    comparison = ComparisonReport(reports={"s0": s0, "s1": s1})

    assert "agent_model:s0!=s1" in comparison.comparability_issues()
    assert "missing:database_snapshot_id:s0" in comparison.comparability_issues()
    output_path = tmp_path / "comparison.md"
    comparison.save_markdown(output_path)
    assert "是否可公平比较：否" in output_path.read_text(encoding="utf-8")

    sanitized_path = tmp_path / "sanitized-report.json"
    s0.results[0].agent_artifact.preview_rows = [{"sensitive": "value"}]
    s0.save_json(sanitized_path)
    loaded = load_report(str(sanitized_path))
    assert loaded.results[0].agent_artifact.preview_rows == []


def test_repeat_stability_reports_volatile_case_ids() -> None:
    first = _make_result(
        test_case_id="same-case",
        passed=True,
        agent_success=True,
        score=1.0,
        execution_time_ms=100.0,
    )
    first.metadata["business_result_correct"] = True
    second = first.model_copy(deep=True)
    second.metadata["business_result_correct"] = False
    report = EvaluationReport(dataset_name="repeats", results=[first, second])

    assert report.repeat_stability() == {
        "repeated_case_count": 1,
        "consistent_case_count": 0,
        "consistency_rate": 0.0,
        "volatile_case_ids": ["same-case"],
    }
