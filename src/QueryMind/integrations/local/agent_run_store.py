"""Local Agent run stores for tests and single-process development."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from QueryMind.core.agent_run import (
    AgentRun,
    AgentRunEvent,
    AgentRunIdempotencyConflictError,
    AgentRunNotFoundError,
    AgentRunStatus,
    AgentRunStore,
    AgentRunTransitionError,
    AgentRunVersionConflictError,
    can_transition_run,
)

_RUN_ID_RE = re.compile(r"run_[0-9a-f]{32}")


def _new_run(
    *,
    tenant_id: str,
    user_id: str,
    database_id: str,
    question_redacted: str,
    conversation_id: str | None,
) -> AgentRun:
    return AgentRun(
        id=f"run_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        user_id=user_id,
        database_id=database_id,
        question_redacted=question_redacted,
        conversation_id=conversation_id,
    )


def _created_event(run: AgentRun) -> AgentRunEvent:
    return AgentRunEvent(
        run_id=run.id,
        sequence=1,
        event_type="run.created",
        data={"status": run.status.value, "stage": run.current_stage},
    )


def _apply_transition(
    run: AgentRun,
    events: list[AgentRunEvent],
    target_status: AgentRunStatus,
    *,
    stage: str | None,
    expected_version: int | None,
    event_type: str,
    event_data: dict[str, Any] | None,
) -> AgentRun:
    if run.status == target_status:
        return run
    if expected_version is not None and run.version != expected_version:
        raise AgentRunVersionConflictError(
            f"Expected run version {expected_version}, found {run.version}"
        )
    if not can_transition_run(run.status, target_status):
        raise AgentRunTransitionError(
            f"Cannot transition Agent run from {run.status.value} to {target_status.value}"
        )

    previous_status = run.status
    now = datetime.now(timezone.utc)
    run.status = target_status
    run.current_stage = stage or target_status.value
    run.version += 1
    run.updated_at = now
    if target_status == AgentRunStatus.RUNNING and run.started_at is None:
        run.started_at = now
    if target_status in {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.CANCELLED,
    }:
        run.completed_at = now

    payload = {
        **(event_data or {}),
        "previous_status": previous_status.value,
        "status": target_status.value,
        "stage": run.current_stage,
    }
    events.append(
        AgentRunEvent(
            run_id=run.id,
            sequence=len(events) + 1,
            event_type=event_type,
            data=payload,
        )
    )
    return run


class MemoryAgentRunStore(AgentRunStore):
    """Async-safe in-memory store for route and domain tests."""

    def __init__(self) -> None:
        self._records: dict[str, tuple[AgentRun, list[AgentRunEvent]]] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

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
    ) -> tuple[AgentRun, bool]:
        scope = (tenant_id, user_id, idempotency_key)
        async with self._lock:
            existing = self._idempotency.get(scope)
            if existing is not None:
                run_id, stored_fingerprint = existing
                if stored_fingerprint != request_fingerprint:
                    raise AgentRunIdempotencyConflictError(
                        "Idempotency key was already used for a different request"
                    )
                return self._records[run_id][0].model_copy(deep=True), False

            run = _new_run(
                tenant_id=tenant_id,
                user_id=user_id,
                database_id=database_id,
                question_redacted=question_redacted,
                conversation_id=conversation_id,
            )
            self._records[run.id] = (run, [_created_event(run)])
            self._idempotency[scope] = (run.id, request_fingerprint)
            return run.model_copy(deep=True), True

    async def get_run(self, run_id: str, *, tenant_id: str, user_id: str) -> AgentRun | None:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None:
                return None
            run = record[0]
            if run.tenant_id != tenant_id or run.user_id != user_id:
                return None
            return run.model_copy(deep=True)

    async def list_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        after_sequence: int = 0,
    ) -> list[AgentRunEvent]:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None or record[0].tenant_id != tenant_id or record[0].user_id != user_id:
                raise AgentRunNotFoundError("Agent run not found")
            return [
                event.model_copy(deep=True)
                for event in record[1]
                if event.sequence > after_sequence
            ]

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
    ) -> AgentRun:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None or record[0].tenant_id != tenant_id or record[0].user_id != user_id:
                raise AgentRunNotFoundError("Agent run not found")
            run = _apply_transition(
                record[0],
                record[1],
                target_status,
                stage=stage,
                expected_version=expected_version,
                event_type=event_type,
                event_data=event_data,
            )
            return run.model_copy(deep=True)


class FileSystemAgentRunStore(AgentRunStore):
    """Atomic JSON store for one-process development and restart recovery.

    The implementation uses a process-local lock. It deliberately does not
    claim multi-process transaction guarantees; a database-backed adapter is
    required before production admission.
    """

    def __init__(self, base_dir: str = "agent_runs") -> None:
        self.base_dir = Path(base_dir)
        self.runs_dir = self.base_dir / "runs"
        self.index_path = self.base_dir / "idempotency.json"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected object in {path}")
        return payload

    @staticmethod
    def _idempotency_fingerprint(tenant_id: str, user_id: str, key: str) -> str:
        payload = f"{tenant_id}\0{user_id}\0{key}".encode()
        return hashlib.sha256(payload).hexdigest()

    def _record_path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise AgentRunNotFoundError("Agent run not found")
        return self.runs_dir / f"{run_id}.json"

    def _load_record(self, run_id: str) -> tuple[AgentRun, list[AgentRunEvent]]:
        payload = self._read_json(self._record_path(run_id))
        if not payload:
            raise AgentRunNotFoundError("Agent run not found")
        run = AgentRun.model_validate(payload.get("run"))
        events = [AgentRunEvent.model_validate(item) for item in payload.get("events", [])]
        return run, events

    def _save_record(self, run: AgentRun, events: list[AgentRunEvent]) -> None:
        self._write_json_atomic(
            self._record_path(run.id),
            {
                "run": run.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
            },
        )

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
    ) -> tuple[AgentRun, bool]:
        fingerprint = self._idempotency_fingerprint(tenant_id, user_id, idempotency_key)
        with self._lock:
            index = self._read_json(self.index_path)
            existing = index.get(fingerprint)
            if isinstance(existing, dict):
                if existing.get("request_fingerprint") != request_fingerprint:
                    raise AgentRunIdempotencyConflictError(
                        "Idempotency key was already used for a different request"
                    )
                run, _ = self._load_record(str(existing.get("run_id") or ""))
                return run, False

            run = _new_run(
                tenant_id=tenant_id,
                user_id=user_id,
                database_id=database_id,
                question_redacted=question_redacted,
                conversation_id=conversation_id,
            )
            self._save_record(run, [_created_event(run)])
            index[fingerprint] = {
                "run_id": run.id,
                "request_fingerprint": request_fingerprint,
            }
            self._write_json_atomic(self.index_path, index)
            return run, True

    async def get_run(self, run_id: str, *, tenant_id: str, user_id: str) -> AgentRun | None:
        with self._lock:
            try:
                run, _ = self._load_record(run_id)
            except AgentRunNotFoundError:
                return None
            if run.tenant_id != tenant_id or run.user_id != user_id:
                return None
            return run

    async def list_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        user_id: str,
        after_sequence: int = 0,
    ) -> list[AgentRunEvent]:
        with self._lock:
            run, events = self._load_record(run_id)
            if run.tenant_id != tenant_id or run.user_id != user_id:
                raise AgentRunNotFoundError("Agent run not found")
            return [event for event in events if event.sequence > after_sequence]

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
    ) -> AgentRun:
        with self._lock:
            run, events = self._load_record(run_id)
            if run.tenant_id != tenant_id or run.user_id != user_id:
                raise AgentRunNotFoundError("Agent run not found")
            original_version = run.version
            run = _apply_transition(
                run,
                events,
                target_status,
                stage=stage,
                expected_version=expected_version,
                event_type=event_type,
                event_data=event_data,
            )
            if run.version != original_version:
                self._save_record(run, events)
            return run
