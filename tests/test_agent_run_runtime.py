from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from QueryMind.core.agent_run import AgentRunGateDecision, AgentRunInput, AgentRunStatus
from QueryMind.core.user import User
from QueryMind.integrations.local import MemoryAgentRunStore
from QueryMind.server.base import ChatHandler, classify_run_exception
from QueryMind.server.fastapi import QueryMindFastAPIServer


class _Resolver:
    async def resolve_user(self, request_context):
        return User(
            id="user-a",
            metadata={"tenant_id": "tenant-a"},
            group_memberships=["user"],
        )


class _RuntimeAgent:
    def __init__(self, policy=None, *, delay: float = 0.0) -> None:
        self.user_resolver = _Resolver()
        self.agent_run_store = MemoryAgentRunStore()
        self.agent_run_policy = policy
        self.delay = delay

    async def send_message(self, request_context, message, *, conversation_id=None):
        sink = request_context.runtime["event_sink"]
        await sink("schema.retrieved", {"table_count": 2})
        await sink(
            "tool.completed",
            {
                "tool_name": "run_sql",
                "success": True,
                "metadata": {
                    "result_validation": {
                        "status": "passed",
                        "issues": [],
                        "evidence": {
                            "row_count": 1,
                            "column_count": 1,
                        },
                    }
                },
                "arguments": {"password": "must-not-leak"},
                "result_rows": [{"email": "alice@example.com"}],
            },
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        yield {"type": "text", "content": f"done:{message}"}


class _FailingRuntimeAgent(_RuntimeAgent):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def send_message(self, request_context, message, *, conversation_id=None):
        if False:  # pragma: no cover - keep the async-generator contract
            yield None
        raise self.error


class _UnvalidatedRuntimeAgent(_RuntimeAgent):
    async def send_message(self, request_context, message, *, conversation_id=None):
        sink = request_context.runtime["event_sink"]
        await sink(
            "tool.completed",
            {
                "tool_name": "run_sql",
                "success": True,
                "metadata": {},
            },
        )
        yield {"type": "text", "content": f"done:{message}"}


def _wait_for_status(client: TestClient, run_id: str, expected: str) -> dict:
    deadline = time.time() + 2
    while time.time() < deadline:
        response = client.get(f"/api/querymind/v1/agent-runs/{run_id}")
        body = response.json()["run"]
        if body["status"] == expected:
            return body
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected}")


def test_background_run_executes_and_persists_redacted_trace() -> None:
    agent = _RuntimeAgent()
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "统计收入", "database_id": "chinook"},
            headers={"Idempotency-Key": "runtime-1"},
        )
        run_id = created.json()["run"]["id"]
        completed = _wait_for_status(client, run_id, "succeeded")
        assert completed["trace_id"].startswith("trace_")

        events = client.get(
            f"/api/querymind/v1/agent-runs/{run_id}/events"
        ).json()["events"]
        assert [event["sequence"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert "schema.retrieved" in [event["event_type"] for event in events]
        assert "result.validated" in [event["event_type"] for event in events]
        serialized = str(events)
        assert "must-not-leak" not in serialized
        assert "alice@example.com" not in serialized

        with client.stream(
            "GET",
            f"/api/querymind/v1/agent-runs/{run_id}/events/stream",
        ) as response:
            stream_text = "".join(response.iter_text())
        assert "[DONE]" in stream_text


def test_background_run_fails_closed_without_result_validation_evidence() -> None:
    agent = _UnvalidatedRuntimeAgent()
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "统计收入", "database_id": "chinook"},
            headers={"Idempotency-Key": "runtime-unvalidated-1"},
        )
        run_id = created.json()["run"]["id"]
        failed = _wait_for_status(client, run_id, "failed")
        events = client.get(
            f"/api/querymind/v1/agent-runs/{run_id}/events"
        ).json()["events"]

    event_types = [event["event_type"] for event in events]
    assert "result.validated" not in event_types
    assert "result.validation_failed" in event_types
    assert failed["error_code"] == "result_validation_missing"
    assert failed["error_category"] == "result_validation"


def test_approval_pauses_then_resumes_without_duplicate_execution() -> None:
    calls = 0

    def policy(question, database_id, user):
        return AgentRunGateDecision(
            action="approve",
            reason_code="sensitive_domain",
            message="需要数据负责人批准",
            risk_level="high",
        )

    agent = _RuntimeAgent(policy)
    original_send = agent.send_message

    async def counted_send(*args, **kwargs):
        nonlocal calls
        calls += 1
        async for item in original_send(*args, **kwargs):
            yield item

    agent.send_message = counted_send
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "统计敏感明细", "database_id": "chinook"},
            headers={"Idempotency-Key": "approval-1"},
        )
        run_id = created.json()["run"]["id"]
        waiting = _wait_for_status(client, run_id, "waiting_for_approval")
        assert calls == 0

        approved = client.post(
            f"/api/querymind/v1/agent-runs/{run_id}/approvals",
            json={"decision": "approve", "expected_version": waiting["version"]},
        )
        assert approved.status_code == 200
        _wait_for_status(client, run_id, "succeeded")
        assert calls == 1


def test_clarification_reject_cancel_and_feedback_boundaries() -> None:
    decisions = {
        "clarify": AgentRunGateDecision(
            action="clarify",
            reason_code="ambiguous_metric",
            message="请确认收入口径",
        ),
        "reject": AgentRunGateDecision(
            action="reject",
            reason_code="absolute_policy_block",
            risk_level="blocked",
        ),
    }

    def policy(question, database_id, user):
        return decisions[question]

    agent = _RuntimeAgent(policy)
    app = QueryMindFastAPIServer(agent).create_app()
    with TestClient(app) as client:
        clarified = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "clarify", "database_id": "chinook"},
            headers={"Idempotency-Key": "clarify-1"},
        )
        clarify_id = clarified.json()["run"]["id"]
        waiting = _wait_for_status(client, clarify_id, "waiting_for_clarification")
        resumed = client.post(
            f"/api/querymind/v1/agent-runs/{clarify_id}/clarifications",
            json={"answer": "使用含税收入", "expected_version": waiting["version"]},
        )
        assert resumed.status_code == 200
        _wait_for_status(client, clarify_id, "succeeded")

        feedback = client.post(
            f"/api/querymind/v1/agent-runs/{clarify_id}/feedback",
            json={"rating": "correct", "reason": "结果符合预期"},
        )
        assert feedback.status_code == 202
        assert "结果符合预期" not in str(feedback.json())

        rejected = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "reject", "database_id": "chinook"},
            headers={"Idempotency-Key": "reject-1"},
        )
        reject_id = rejected.json()["run"]["id"]
        _wait_for_status(client, reject_id, "rejected")
        assert (
            client.post(
                f"/api/querymind/v1/agent-runs/{reject_id}/feedback",
                json={"rating": "incorrect"},
            ).status_code
            == 202
        )

        queued_store = MemoryAgentRunStore()
        queued_agent = _RuntimeAgent()
        queued_agent.agent_run_store = queued_store
        queued, _ = asyncio.run(
            queued_store.create_run(
                tenant_id="tenant-a",
                user_id="user-a",
                database_id="chinook",
                question_redacted="queued",
                idempotency_key="queued-1",
                request_fingerprint="queued-1",
            )
        )
        cancelled = asyncio.run(
            queued_store.transition_run(
                queued.id,
                AgentRunStatus.CANCELLED,
                tenant_id="tenant-a",
                user_id="user-a",
            )
        )
        assert cancelled.status == AgentRunStatus.CANCELLED


def test_fault_injection_classifies_timeout_and_fails_closed() -> None:
    agent = _FailingRuntimeAgent(asyncio.TimeoutError("provider timed out"))
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "统计收入", "database_id": "chinook"},
            headers={"Idempotency-Key": "fault-timeout-1"},
        )
        run_id = created.json()["run"]["id"]
        failed = _wait_for_status(client, run_id, "failed")

    assert failed["error_code"] == "provider_timeout"
    assert failed["error_category"] == "provider_transient"


def test_fault_classifier_keeps_permanent_and_permission_failures_distinct() -> None:
    assert classify_run_exception(PermissionError("denied")) == (
        "permission_denied",
        "permission_denied",
    )
    assert classify_run_exception(ValueError("bad model")) == (
        "invalid_runtime_input",
        "provider_permanent",
    )


def test_startup_fails_closed_for_interrupted_running_work() -> None:
    agent = _RuntimeAgent()
    run, _ = asyncio.run(
        agent.agent_run_store.create_run(
            tenant_id="tenant-a",
            user_id="user-a",
            database_id="chinook",
            question_redacted="统计收入",
            idempotency_key="restart-1",
            request_fingerprint="restart-1",
            input_payload=AgentRunInput(
                question="统计收入",
                user={"id": "user-a", "metadata": {"tenant_id": "tenant-a"}},
            ),
        )
    )
    asyncio.run(
        agent.agent_run_store.transition_run(
            run.id,
            AgentRunStatus.RUNNING,
            tenant_id="tenant-a",
            user_id="user-a",
        )
    )
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        recovered = client.get(f"/api/querymind/v1/agent-runs/{run.id}").json()["run"]
        events = client.get(
            f"/api/querymind/v1/agent-runs/{run.id}/events"
        ).json()["events"]

    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "execution_state_unknown"
    assert events[-1]["event_type"] == "run.recovery_failed_closed"


def test_startup_resubmits_durable_queued_work() -> None:
    agent = _RuntimeAgent()
    run, _ = asyncio.run(
        agent.agent_run_store.create_run(
            tenant_id="tenant-a",
            user_id="user-a",
            database_id="chinook",
            question_redacted="统计收入",
            idempotency_key="restart-queued-1",
            request_fingerprint="restart-queued-1",
            input_payload=AgentRunInput(
                question="统计收入",
                user={"id": "user-a", "metadata": {"tenant_id": "tenant-a"}},
            ),
        )
    )
    app = QueryMindFastAPIServer(agent).create_app()

    with TestClient(app) as client:
        recovered = _wait_for_status(client, run.id, "succeeded")

    assert recovered["result_summary_redacted"] == "Completed with 1 response components"


def test_only_server_runtime_can_inject_a_resolved_user() -> None:
    handler = ChatHandler(agent=object())
    untrusted = handler._create_request_context(
        conversation_id="conv-1",
        request_id="req-1",
        metadata={"user": {"id": "client-claimed-admin"}},
    )
    trusted_user = User(id="server-user")
    trusted = handler._create_request_context(
        conversation_id="conv-2",
        request_id="req-2",
        runtime={"resolved_user": trusted_user},
    )

    assert untrusted.user is None
    assert trusted.user == trusted_user
