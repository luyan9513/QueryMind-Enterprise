from __future__ import annotations

import asyncio

import pytest

from QueryMind.core.agent_run import (
    AgentRunIdempotencyConflictError,
    AgentRunNotFoundError,
    AgentRunStatus,
    AgentRunTransitionError,
    AgentRunVersionConflictError,
    can_transition_run,
    redact_question_preview,
)
from QueryMind.integrations.local import FileSystemAgentRunStore, MemoryAgentRunStore


def test_agent_run_transition_policy_is_bounded() -> None:
    assert can_transition_run(AgentRunStatus.QUEUED, AgentRunStatus.RUNNING)
    assert can_transition_run(AgentRunStatus.RUNNING, AgentRunStatus.WAITING_FOR_APPROVAL)
    assert can_transition_run(AgentRunStatus.WAITING_FOR_APPROVAL, AgentRunStatus.REJECTED)
    assert not can_transition_run(AgentRunStatus.SUCCEEDED, AgentRunStatus.RUNNING)
    assert can_transition_run(AgentRunStatus.CANCELLED, AgentRunStatus.CANCELLED)


def test_question_preview_redacts_common_identifiers() -> None:
    preview = redact_question_preview(
        "查询 alice@example.com 在订单 'SO-12345' 和客户 123456 的收入"
    )

    assert "alice@example.com" not in preview
    assert "SO-12345" not in preview
    assert "123456" not in preview
    assert "[EMAIL]" in preview
    assert "[REDACTED]" in preview


def test_memory_store_enforces_scoped_idempotency_and_versions() -> None:
    async def _run() -> None:
        store = MemoryAgentRunStore()
        first, created = await store.create_run(
            tenant_id="tenant-a",
            user_id="user-a",
            database_id="chinook",
            question_redacted="统计收入",
            conversation_id="conv-1",
            idempotency_key="request-1",
            request_fingerprint="fingerprint-1",
        )
        repeated, repeated_created = await store.create_run(
            tenant_id="tenant-a",
            user_id="user-a",
            database_id="chinook",
            question_redacted="统计收入",
            conversation_id="conv-1",
            idempotency_key="request-1",
            request_fingerprint="fingerprint-1",
        )

        assert created is True
        assert repeated_created is False
        assert repeated.id == first.id

        with pytest.raises(AgentRunIdempotencyConflictError):
            await store.create_run(
                tenant_id="tenant-a",
                user_id="user-a",
                database_id="chinook",
                question_redacted="另一个问题",
                idempotency_key="request-1",
                request_fingerprint="fingerprint-2",
            )

        with pytest.raises(AgentRunVersionConflictError):
            await store.transition_run(
                first.id,
                AgentRunStatus.RUNNING,
                tenant_id="tenant-a",
                user_id="user-a",
                expected_version=99,
            )

        running = await store.transition_run(
            first.id,
            AgentRunStatus.RUNNING,
            tenant_id="tenant-a",
            user_id="user-a",
            expected_version=1,
        )
        assert running.version == 2
        assert running.started_at is not None

        succeeded = await store.transition_run(
            first.id,
            AgentRunStatus.SUCCEEDED,
            tenant_id="tenant-a",
            user_id="user-a",
            expected_version=2,
        )
        assert succeeded.version == 3
        assert succeeded.completed_at is not None

        with pytest.raises(AgentRunTransitionError):
            await store.transition_run(
                first.id,
                AgentRunStatus.RUNNING,
                tenant_id="tenant-a",
                user_id="user-a",
            )

    asyncio.run(_run())


def test_file_store_survives_reopen_and_keeps_events_ordered(tmp_path) -> None:
    async def _run() -> None:
        base_dir = tmp_path / "agent_runs"
        store = FileSystemAgentRunStore(str(base_dir))
        run, created = await store.create_run(
            tenant_id="tenant-a",
            user_id="user-a",
            database_id="chinook",
            question_redacted="统计收入",
            idempotency_key="secret-client-key",
            request_fingerprint="fingerprint-1",
        )
        assert created is True

        cancelled = await store.transition_run(
            run.id,
            AgentRunStatus.CANCELLED,
            tenant_id="tenant-a",
            user_id="user-a",
            expected_version=1,
            event_type="run.cancelled",
        )
        assert cancelled.version == 2

        reopened = FileSystemAgentRunStore(str(base_dir))
        restored = await reopened.get_run(
            run.id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        assert restored is not None
        assert restored.status == AgentRunStatus.CANCELLED

        events = await reopened.list_events(
            run.id,
            tenant_id="tenant-a",
            user_id="user-a",
        )
        assert [event.sequence for event in events] == [1, 2]
        assert [event.event_type for event in events] == [
            "run.created",
            "run.cancelled",
        ]
        assert [
            event.sequence
            for event in await reopened.list_events(
                run.id,
                tenant_id="tenant-a",
                user_id="user-a",
                after_sequence=1,
            )
        ] == [2]

        assert (
            await reopened.get_run(
                run.id,
                tenant_id="tenant-a",
                user_id="user-b",
            )
            is None
        )
        with pytest.raises(AgentRunNotFoundError):
            await reopened.list_events(
                run.id,
                tenant_id="tenant-a",
                user_id="user-b",
            )
        assert await reopened.get_run(
            "../idempotency",
            tenant_id="tenant-a",
            user_id="user-a",
        ) is None

        index_text = (base_dir / "idempotency.json").read_text(encoding="utf-8")
        assert "secret-client-key" not in index_text

    asyncio.run(_run())
