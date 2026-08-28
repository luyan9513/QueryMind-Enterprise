from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from QueryMind.core.agent_run import AgentRunStatus
from QueryMind.core.user import User
from QueryMind.integrations.local import MemoryAgentRunStore
from QueryMind.server.fastapi import QueryMindFastAPIServer
from QueryMind.server.fastapi.agent_run_routes import register_agent_run_routes


class _HeaderUserResolver:
    async def resolve_user(self, request_context):
        headers = request_context.metadata["headers"]
        return User(
            id=headers.get("x-user-id", "user-a"),
            metadata={"tenant_id": headers.get("x-tenant-id", "tenant-a")},
            group_memberships=["user"],
        )


class _DummyAgent:
    def __init__(self, run_store=None) -> None:
        self.user_resolver = _HeaderUserResolver()
        self.agent_run_store = run_store


def _build_client(store=None) -> TestClient:
    app = FastAPI()
    register_agent_run_routes(app, _DummyAgent(store), store)
    return TestClient(app)


def test_agent_run_api_is_idempotent_scoped_and_cancellable() -> None:
    client = _build_client(MemoryAgentRunStore())
    request = {
        "question": "查询 alice@example.com 的 2025 年收入",
        "database_id": "chinook",
        "conversation_id": "conv-1",
    }
    headers = {"Idempotency-Key": "client-request-1"}

    created = client.post(
        "/api/querymind/v1/agent-runs",
        json=request,
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["created"] is True
    assert body["run"]["status"] == "queued"
    assert "alice@example.com" not in body["run"]["question_redacted"]
    run_id = body["run"]["id"]

    repeated = client.post(
        "/api/querymind/v1/agent-runs",
        json=request,
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["run"]["id"] == run_id

    conflict = client.post(
        "/api/querymind/v1/agent-runs",
        json={**request, "question": "另一个问题"},
        headers=headers,
    )
    assert conflict.status_code == 409

    hidden = client.get(
        f"/api/querymind/v1/agent-runs/{run_id}",
        headers={"x-user-id": "user-b"},
    )
    assert hidden.status_code == 404

    cancelled = client.post(
        f"/api/querymind/v1/agent-runs/{run_id}/cancel",
        json={"expected_version": 1},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert cancelled.json()["run"]["version"] == 2

    repeated_cancel = client.post(
        f"/api/querymind/v1/agent-runs/{run_id}/cancel",
        json={},
    )
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json()["run"]["version"] == 2

    events = client.get(f"/api/querymind/v1/agent-runs/{run_id}/events")
    assert events.status_code == 200
    assert [event["sequence"] for event in events.json()["events"]] == [1, 2]
    assert events.json()["next_after"] == 2

    remaining = client.get(
        f"/api/querymind/v1/agent-runs/{run_id}/events",
        params={"after": 1},
    )
    assert [event["sequence"] for event in remaining.json()["events"]] == [2]


def test_agent_run_api_requires_store_and_idempotency_key() -> None:
    client = _build_client(None)
    payload = {"question": "统计收入", "database_id": "chinook"}

    missing_key = client.post("/api/querymind/v1/agent-runs", json=payload)
    assert missing_key.status_code == 422

    unavailable = client.post(
        "/api/querymind/v1/agent-runs",
        json=payload,
        headers={"Idempotency-Key": "request-1"},
    )
    assert unavailable.status_code == 503

    blank_question = client.post(
        "/api/querymind/v1/agent-runs",
        json={"question": "   ", "database_id": "chinook"},
        headers={"Idempotency-Key": "request-2"},
    )
    assert blank_question.status_code == 422

    blank_key = client.post(
        "/api/querymind/v1/agent-runs",
        json=payload,
        headers={"Idempotency-Key": "   "},
    )
    assert blank_key.status_code == 400


def test_fastapi_server_factory_registers_agent_run_routes() -> None:
    store = MemoryAgentRunStore()
    app = QueryMindFastAPIServer(_DummyAgent(store)).create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/querymind/v1/agent-runs",
            json={"question": "统计收入", "database_id": "chinook"},
            headers={"Idempotency-Key": "factory-request-1"},
        )

    assert response.status_code == 201
    assert response.json()["run"]["status"] == "queued"


def test_human_decision_and_feedback_routes_hide_cross_user_runs() -> None:
    store = MemoryAgentRunStore()
    client = _build_client(store)
    created = client.post(
        "/api/querymind/v1/agent-runs",
        json={"question": "统计收入", "database_id": "chinook"},
        headers={"Idempotency-Key": "scoped-hitl-1"},
    )
    run_id = created.json()["run"]["id"]

    asyncio.run(
        store.transition_run(
            run_id,
            AgentRunStatus.RUNNING,
            tenant_id="tenant-a",
            user_id="user-a",
        )
    )
    waiting = asyncio.run(
        store.transition_run(
            run_id,
            AgentRunStatus.WAITING_FOR_APPROVAL,
            tenant_id="tenant-a",
            user_id="user-a",
        )
    )

    hidden_approval = client.post(
        f"/api/querymind/v1/agent-runs/{run_id}/approvals",
        json={"decision": "approve", "expected_version": waiting.version},
        headers={"x-user-id": "user-b"},
    )
    hidden_feedback = client.post(
        f"/api/querymind/v1/agent-runs/{run_id}/feedback",
        json={"rating": "incorrect"},
        headers={"x-user-id": "user-b"},
    )

    assert hidden_approval.status_code == 404
    assert hidden_feedback.status_code == 404
