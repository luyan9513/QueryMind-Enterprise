"""Storage contract and errors for Agent runs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import AgentRun, AgentRunEvent, AgentRunInput, AgentRunStatus


class AgentRunStoreError(Exception):
    """Base error for Agent run persistence."""


class AgentRunNotFoundError(AgentRunStoreError):
    """Raised when a scoped Agent run does not exist."""


class AgentRunIdempotencyConflictError(AgentRunStoreError):
    """Raised when an idempotency key is reused for a different request."""


class AgentRunTransitionError(AgentRunStoreError):
    """Raised when a lifecycle transition is invalid."""


class AgentRunVersionConflictError(AgentRunStoreError):
    """Raised when optimistic concurrency detects a stale version."""


class AgentRunStore(ABC):
    """Persistence boundary for Agent run snapshots and events."""

    @abstractmethod
    async def create_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        database_id: str,
        question_redacted: str,
        idempotency_key: str,
        request_fingerprint: str,
        conversation_id: str | None = None,
        input_payload: AgentRunInput | None = None,
    ) -> tuple[AgentRun, bool]:
        """Create a run or return the prior run for the same request."""

    @abstractmethod
    async def get_run(self, run_id: str, *, tenant_id: str, user_id: str) -> AgentRun | None:
        """Get a run only within its tenant and user scope."""

    @abstractmethod
    async def list_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        after_sequence: int = 0,
    ) -> list[AgentRunEvent]:
        """Return ordered events after the provided sequence."""

    @abstractmethod
    async def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        tenant_id: str,
        user_id: str,
        data: dict[str, Any] | None = None,
    ) -> AgentRunEvent:
        """Append one ordered, redacted event without changing run status."""

    @abstractmethod
    async def get_run_input(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> AgentRunInput:
        """Load private execution input for the owning tenant and user."""

    @abstractmethod
    async def update_run_input(
        self,
        run_id: str,
        payload: AgentRunInput,
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        """Atomically update private execution input."""

    @abstractmethod
    async def resolve_gate(
        self,
        run_id: str,
        target_status: AgentRunStatus,
        input_payload: AgentRunInput,
        *,
        tenant_id: str,
        user_id: str,
        stage: str,
        expected_version: int,
        event_type: str,
        event_data: dict[str, Any] | None = None,
    ) -> AgentRun:
        """Atomically persist a human decision and its lifecycle transition."""

    @abstractmethod
    async def append_feedback(
        self,
        run_id: str,
        feedback: dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        """Append one private feedback record without losing concurrent writes."""

    @abstractmethod
    async def list_active_runs(self) -> list[AgentRun]:
        """List non-terminal runs for startup recovery."""

    @abstractmethod
    async def transition_run(
        self,
        run_id: str,
        target_status: AgentRunStatus,
        *,
        tenant_id: str,
        user_id: str,
        stage: str | None = None,
        expected_version: int | None = None,
        event_type: str = "run.status_changed",
        event_data: dict[str, Any] | None = None,
        snapshot_updates: dict[str, Any] | None = None,
    ) -> AgentRun:
        """Atomically validate, persist, and emit a state transition."""
