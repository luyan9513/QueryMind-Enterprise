"""Domain models for durable Agent run state."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentRunStatus(str, Enum):
    """Lifecycle states for an Agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_CLARIFICATION = "waiting_for_clarification"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.REJECTED,
    AgentRunStatus.CANCELLED,
}

ALLOWED_RUN_TRANSITIONS = {
    AgentRunStatus.QUEUED: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.RUNNING: {
        AgentRunStatus.WAITING_FOR_CLARIFICATION,
        AgentRunStatus.WAITING_FOR_APPROVAL,
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.WAITING_FOR_CLARIFICATION: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.WAITING_FOR_APPROVAL: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.REJECTED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.SUCCEEDED: set(),
    AgentRunStatus.FAILED: set(),
    AgentRunStatus.REJECTED: set(),
    AgentRunStatus.CANCELLED: set(),
}


def can_transition_run(current: AgentRunStatus, target: AgentRunStatus) -> bool:
    """Return whether a lifecycle transition is valid.

    Repeating the same state is treated as an idempotent no-op.
    """

    return current == target or target in ALLOWED_RUN_TRANSITIONS[current]


class AgentRun(BaseModel):
    """Persisted, user-scoped Agent run snapshot."""

    id: str = Field(description="Unique Agent run identifier")
    tenant_id: str = Field(description="Tenant scope resolved by the server")
    user_id: str = Field(description="Owning user identifier")
    conversation_id: str | None = None
    database_id: str = Field(description="Selected data-source identifier")
    question_redacted: str = Field(description="Redacted question preview")
    status: AgentRunStatus = AgentRunStatus.QUEUED
    current_stage: str = "queued"
    risk_level: str = "normal"
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_category: str | None = None


class AgentRunEvent(BaseModel):
    """Ordered, redacted lifecycle event for one Agent run."""

    run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_QUOTED_VALUE_RE = re.compile(r"(['\"])(?:(?!\1).){1,200}\1")
_LONG_NUMBER_RE = re.compile(r"(?<!\w)\d{4,}(?!\w)")


def redact_question_preview(question: str, max_length: int = 500) -> str:
    """Create a compact preview without common literal identifiers."""

    value = " ".join(str(question or "").split())
    value = _EMAIL_RE.sub("[EMAIL]", value)
    value = _QUOTED_VALUE_RE.sub("'[REDACTED]'", value)
    value = _LONG_NUMBER_RE.sub("[NUMBER]", value)
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "…"
