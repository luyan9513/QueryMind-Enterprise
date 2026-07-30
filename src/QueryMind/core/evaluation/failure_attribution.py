"""Stable primary/secondary failure attribution for evaluation cases."""

from __future__ import annotations

from typing import List

from .base import EvaluationResult


_PROVIDER_MARKERS = (
    "timeout",
    "timed out",
    "rate limit",
    "429",
    "ssl",
    "connection",
    "service unavailable",
)
_PERMISSION_MARKERS = (
    "insufficient group access",
    "permission denied",
    "not authorized",
    "no territory access",
)


def classify_failure(result: EvaluationResult) -> tuple[str, List[str]]:
    """Return one primary category and ordered secondary categories."""
    categories: List[str] = []
    issue_tags = set(result.issue_tags)

    if "ground_truth_failure" in issue_tags or result.metadata.get("failure_type") == "dataset_error":
        categories.append("dataset_failure")

    agent_error = str(result.agent_result.error or "").lower()
    if agent_error and any(marker in agent_error for marker in _PROVIDER_MARKERS):
        categories.append("provider_failure")
    if "judge_request_failed" in issue_tags:
        categories.append("provider_failure")

    tool_texts = []
    for record in result.agent_result.tool_calls:
        tool_texts.append(str(record.result_text or "").lower())
        tool_texts.append(str(record.metadata.get("tool_error") or "").lower())
        stage = str(record.metadata.get("rejection_stage") or "").lower()
        if stage == "permission":
            categories.append("permission_failure")
        elif stage == "semantic_contract":
            categories.append("semantic_contract_failure")
        elif stage:
            categories.append("governance_rejection")
        query_plan_issues = record.metadata.get("query_plan_issues") or []
        if any("contract_" in str(issue) for issue in query_plan_issues):
            categories.append("semantic_contract_failure")

    combined_tool_text = "\n".join(tool_texts)
    if any(marker in combined_tool_text for marker in _PERMISSION_MARKERS):
        categories.append("permission_failure")
    if "sql rejected:" in combined_tool_text or "query rejected:" in combined_tool_text:
        categories.append("governance_rejection")

    schema_recall = result.metadata.get("schema_recall")
    if schema_recall is not None and float(schema_recall) < 1.0:
        categories.append("schema_recall_failure")

    if "missing_sql" in issue_tags:
        categories.append("sql_generation_failure")
    if result.metadata.get("sql_contract_passed") is False:
        categories.append("query_contract_failure")
    if "execution_error" in issue_tags or (
        result.agent_artifact is not None and not result.agent_artifact.success
    ):
        categories.append("sql_execution_failure")
    verified_result_correct = result.metadata.get(
        "verified_result_correct",
        result.metadata.get("result_correct"),
    )
    if verified_result_correct is False and result.agent_artifact is not None:
        categories.append("result_mismatch")
    if issue_tags & {"final_answer_absent", "final_answer_fragment_mismatch"}:
        categories.append("answer_format_failure")

    if not categories:
        return ("success", []) if result.passed else ("unknown", [])

    deduped = list(dict.fromkeys(categories))
    if (
        verified_result_correct is True
        and result.metadata.get("sql_contract_passed") is not False
    ):
        return "success", deduped

    priority = [
        "dataset_failure",
        "provider_failure",
        "permission_failure",
        "semantic_contract_failure",
        "sql_generation_failure",
        "query_contract_failure",
        "sql_execution_failure",
        "result_mismatch",
        "schema_recall_failure",
        "governance_rejection",
        "answer_format_failure",
        "unknown",
    ]
    primary = next((name for name in priority if name in deduped), deduped[0])
    return primary, [name for name in deduped if name != primary]


def enrich_failure_attribution(result: EvaluationResult) -> None:
    primary, secondary = classify_failure(result)
    result.metadata["primary_failure"] = primary
    result.metadata["secondary_failures"] = secondary
