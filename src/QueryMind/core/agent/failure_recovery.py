"""Deterministic tool-failure classification for bounded Agent recovery."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .._compat import StrEnum
from ..tool import ToolCall, ToolResult


class FailureCategory(StrEnum):
    TERMINAL_SECURITY = "terminal_security"
    SCHEMA_EVIDENCE = "schema_evidence"
    QUERY_PLANNING = "query_planning"
    SEMANTIC_REVIEW = "semantic_review"
    SQL_REPAIR = "sql_repair"
    TRANSIENT_EXECUTION = "transient_execution"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    STOP = "stop"
    RETRIEVE_SCHEMA = "retrieve_schema"
    REPLAN = "replan"
    REVIEW_SQL = "review_sql"
    REPAIR_SQL = "repair_sql"
    RETRY_ONCE = "retry_once"


@dataclass(slots=True)
class FailureDecision:
    category: FailureCategory
    action: RecoveryAction
    fingerprint: str
    reason: str
    repetition_count: int = 1
    exhausted: bool = False
    allowed_tools: list[str] = field(default_factory=list)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "action": self.action.value,
            "fingerprint": self.fingerprint,
            "reason": self.reason,
            "repetition_count": self.repetition_count,
            "exhausted": self.exhausted,
            "allowed_tools": list(self.allowed_tools),
        }


_TRANSIENT_RE = re.compile(
    r"(?i)(timeout|timed out|temporar|connection reset|connection closed|"
    r"server closed|too many connections|deadlock|serialization failure|rate limit)"
)
_SCHEMA_RE = re.compile(
    r"(?i)(does not exist|undefined (?:table|column)|unknown column|"
    r"no such (?:table|column)|column .* not found|relation .* not found)"
)
_METADATA_RE = re.compile(
    r"(?i)(?:\binformation_schema\b|\bpg_catalog\b|"
    r"\bpg_(?:class|attribute|constraint|namespace|index|indexes)\b|\bsys\.)"
)


def _normalized_sql(tool_call: ToolCall) -> str:
    sql = tool_call.arguments.get("sql")
    if not isinstance(sql, str):
        return ""
    return " ".join(sql.lower().split())


def _fingerprint(tool_call: ToolCall, result: ToolResult) -> str:
    stage = str(result.metadata.get("rejection_stage") or "")
    code = str(result.metadata.get("rejection_code") or "")
    error = " ".join(str(result.error or result.result_for_llm or "").lower().split())
    normalized_sql = _normalized_sql(tool_call)
    if _METADATA_RE.search(normalized_sql):
        normalized_sql = "<metadata_query>"
    payload = "|".join([tool_call.name, stage, code, normalized_sql, error[:240]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def classify_tool_failure(tool_call: ToolCall, result: ToolResult) -> FailureDecision | None:
    """Classify one failed tool result without database- or dataset-specific rules."""
    if result.success:
        return None

    stage = str(result.metadata.get("rejection_stage") or "").strip().lower()
    code = str(result.metadata.get("rejection_code") or "").strip().lower()
    error = str(result.error or result.result_for_llm or "")
    fingerprint = _fingerprint(tool_call, result)

    if stage in {"permission", "injection", "complexity"}:
        return FailureDecision(
            category=FailureCategory.TERMINAL_SECURITY,
            action=RecoveryAction.STOP,
            fingerprint=fingerprint,
            reason=code or stage,
            allowed_tools=[],
        )
    if tool_call.name == "run_sql" and _METADATA_RE.search(_normalized_sql(tool_call)):
        return FailureDecision(
            category=FailureCategory.SCHEMA_EVIDENCE,
            action=RecoveryAction.RETRIEVE_SCHEMA,
            fingerprint=fingerprint,
            reason="metadata_query_rejected",
            allowed_tools=["schema_retrieve"],
        )
    if stage == "planning":
        return FailureDecision(
            category=FailureCategory.QUERY_PLANNING,
            action=RecoveryAction.REPLAN,
            fingerprint=fingerprint,
            reason=code or stage,
            allowed_tools=["schema_retrieve", "submit_query_plan"],
        )
    if stage == "semantic_contract":
        return FailureDecision(
            category=FailureCategory.QUERY_PLANNING,
            action=RecoveryAction.REPLAN,
            fingerprint=fingerprint,
            reason=code or stage,
            allowed_tools=["schema_retrieve", "submit_query_plan"],
        )
    if stage == "semantic_review":
        return FailureDecision(
            category=FailureCategory.SEMANTIC_REVIEW,
            action=RecoveryAction.REVIEW_SQL,
            fingerprint=fingerprint,
            reason=code or stage,
            allowed_tools=[
                "schema_retrieve",
                "submit_query_plan",
                "review_sql_intent",
            ],
        )
    if tool_call.name == "run_sql" and _SCHEMA_RE.search(error):
        return FailureDecision(
            category=FailureCategory.SCHEMA_EVIDENCE,
            action=RecoveryAction.RETRIEVE_SCHEMA,
            fingerprint=fingerprint,
            reason="database_schema_error",
            allowed_tools=["schema_retrieve", "submit_query_plan"],
        )
    if tool_call.name == "run_sql" and _TRANSIENT_RE.search(error):
        return FailureDecision(
            category=FailureCategory.TRANSIENT_EXECUTION,
            action=RecoveryAction.RETRY_ONCE,
            fingerprint=fingerprint,
            reason="transient_database_error",
            allowed_tools=["run_sql"],
        )
    if tool_call.name == "run_sql":
        return FailureDecision(
            category=FailureCategory.SQL_REPAIR,
            action=RecoveryAction.REPAIR_SQL,
            fingerprint=fingerprint,
            reason=code or stage or "sql_execution_error",
            allowed_tools=[
                "schema_retrieve",
                "submit_query_plan",
                "review_sql_intent",
                "run_sql",
            ],
        )
    return FailureDecision(
        category=FailureCategory.UNKNOWN,
        action=RecoveryAction.STOP,
        fingerprint=fingerprint,
        reason=code or stage or "unknown_tool_failure",
        allowed_tools=[],
    )


class FailureRecoveryState:
    """Track repeated failure fingerprints and stop deterministic loops."""

    def __init__(self, *, max_same_failure_retries: int = 2) -> None:
        self.max_same_failure_retries = max(1, int(max_same_failure_retries))
        self.counts: dict[str, int] = {}
        self.pending: FailureDecision | None = None

    def observe(self, tool_call: ToolCall, result: ToolResult) -> FailureDecision | None:
        decision = classify_tool_failure(tool_call, result)
        if decision is None:
            self.pending = None
            return None
        count = self.counts.get(decision.fingerprint, 0) + 1
        self.counts[decision.fingerprint] = count
        decision.repetition_count = count
        if count >= self.max_same_failure_retries:
            decision.exhausted = True
            decision.action = RecoveryAction.STOP
            decision.allowed_tools = []
        self.pending = decision
        return decision


def build_failure_recovery_prompt(decision: FailureDecision) -> str:
    """Build one action-specific prompt block from a structured decision."""
    if decision.exhausted or decision.action == RecoveryAction.STOP:
        return (
            "## Structured Recovery Stopped\n\n"
            f"The runtime classified the failure as `{decision.category.value}` "
            f"and stopped tools after {decision.repetition_count} matching attempt(s). "
            "Do not guess or repeat the same call. Explain the blocked assumption and "
            "ask one concise clarification question."
        )
    instructions = {
        RecoveryAction.RETRIEVE_SCHEMA: (
            "Use one focused schema_retrieve call for the missing physical table or "
            "column. Rebuild the plan from returned evidence before SQL."
        ),
        RecoveryAction.REPLAN: (
            "Resolve every planning issue using schema evidence, then submit a corrected "
            "query plan. Do not retry the rejected SQL unchanged."
        ),
        RecoveryAction.REVIEW_SQL: (
            "Revise the SQL or intent according to the semantic-review feedback, then call "
            "review_sql_intent again. Do not call run_sql before approval."
        ),
        RecoveryAction.REPAIR_SQL: (
            "Repair the SQL using the database error and existing schema evidence. Change "
            "only the failing structure and preserve the requested business grain."
        ),
        RecoveryAction.RETRY_ONCE: (
            "The failure appears transient. Retry the same SQL once; if it fails again, "
            "stop instead of looping."
        ),
    }
    return (
        "## Structured Failure Recovery\n\n"
        f"Category: `{decision.category.value}`. Action: `{decision.action.value}`. "
        + instructions.get(decision.action, "Stop and ask for clarification.")
    )
