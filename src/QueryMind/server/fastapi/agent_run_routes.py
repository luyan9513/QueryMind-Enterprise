"""Versioned Agent run lifecycle routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from QueryMind.core.agent_run import (
    AgentRunIdempotencyConflictError,
    AgentRunInput,
    AgentRunNotFoundError,
    AgentRunStatus,
    AgentRunStore,
    AgentRunTransitionError,
    AgentRunVersionConflictError,
    redact_question_preview,
)
from QueryMind.core.user import User
from QueryMind.core.user.request_context import RequestContext
from QueryMind.server.base.agent_run_executor import AgentRunExecutor


class CreateAgentRunRequest(BaseModel):
    """Input for creating a queued Agent run."""

    question: str = Field(min_length=1, max_length=10_000)
    database_id: str = Field(min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=256)

    @field_validator("question", "database_id")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class CancelAgentRunRequest(BaseModel):
    """Optional optimistic concurrency guard for cancellation."""

    expected_version: int | None = Field(default=None, ge=1)


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class ClarificationRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)

    @field_validator("answer")
    @classmethod
    def reject_blank_answer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer must not be blank")
        return value


class AgentRunFeedbackRequest(BaseModel):
    rating: str = Field(pattern="^(correct|partially_correct|incorrect)$")
    reason: str | None = Field(default=None, max_length=2000)


async def _resolve_current_user(agent: Any, request: Request) -> User:
    user_resolver = getattr(agent, "user_resolver", None)
    if user_resolver is None:
        return User(
            id="admin",
            username="admin",
            email="admin@local",
            group_memberships=["admin"],
        )

    request_context = RequestContext(
        metadata={
            "headers": dict(request.headers),
            "cookies": dict(request.cookies),
        }
    )
    user = await user_resolver.resolve_user(request_context)
    if isinstance(user, User):
        return user
    return User(
        id=getattr(user, "id", "unknown"),
        username=getattr(user, "username", "unknown"),
        email=getattr(user, "email", ""),
        metadata=getattr(user, "metadata", {}),
        group_memberships=getattr(user, "group_memberships", []),
    )


def _tenant_id(user: User) -> str:
    value = str((user.metadata or {}).get("tenant_id") or "default").strip()
    return value[:128] or "default"


def _create_request_fingerprint(payload: CreateAgentRunRequest) -> str:
    canonical = json.dumps(
        {
            "question": " ".join(payload.question.split()),
            "database_id": payload.database_id.strip(),
            "conversation_id": payload.conversation_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_response(run: Any) -> dict[str, Any]:
    return run.model_dump(mode="json")


def register_agent_run_routes(
    app: FastAPI,
    agent: Any,
    run_store: AgentRunStore | None,
    executor: AgentRunExecutor | None = None,
) -> None:
    """Register durable execution, trace, HITL, and feedback APIs."""

    def _store() -> AgentRunStore:
        if run_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent run store not configured",
            )
        return run_store

    @app.post(
        "/api/querymind/v1/agent-runs",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_agent_run(
        payload: CreateAgentRunRequest,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=128),
        ],
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key must not be blank")
        try:
            run, created = await _store().create_run(
                tenant_id=_tenant_id(user),
                user_id=user.id,
                database_id=payload.database_id.strip(),
                question_redacted=redact_question_preview(payload.question),
                conversation_id=payload.conversation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=_create_request_fingerprint(payload),
                input_payload=AgentRunInput(
                    question=payload.question,
                    user=user.model_dump(mode="json"),
                ),
            )
        except AgentRunIdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        if created and executor is not None:
            executor.submit(run.id, tenant_id=_tenant_id(user), user_id=user.id)
        return {"created": created, "run": _run_response(run)}

    @app.get("/api/querymind/v1/agent-runs/{run_id}")
    async def get_agent_run(run_id: str, request: Request) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        run = await _store().get_run(
            run_id,
            tenant_id=_tenant_id(user),
            user_id=user.id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found")
        return {"run": _run_response(run)}

    @app.get("/api/querymind/v1/agent-runs/{run_id}/events")
    async def list_agent_run_events(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        try:
            events = await _store().list_events(
                run_id,
                tenant_id=_tenant_id(user),
                user_id=user.id,
                after_sequence=after,
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Agent run not found") from exc
        return {
            "events": [event.model_dump(mode="json") for event in events],
            "next_after": events[-1].sequence if events else after,
        }

    @app.get("/api/querymind/v1/agent-runs/{run_id}/events/stream")
    async def stream_agent_run_events(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        user = await _resolve_current_user(agent, request)
        tenant_id = _tenant_id(user)
        if await _store().get_run(run_id, tenant_id=tenant_id, user_id=user.id) is None:
            raise HTTPException(status_code=404, detail="Agent run not found")

        async def generate():
            cursor = after
            while True:
                events = await _store().list_events(
                    run_id,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    after_sequence=cursor,
                )
                for event in events:
                    cursor = event.sequence
                    yield f"data: {event.model_dump_json()}\n\n"
                run = await _store().get_run(
                    run_id,
                    tenant_id=tenant_id,
                    user_id=user.id,
                )
                if run is None or run.status in {
                    AgentRunStatus.SUCCEEDED,
                    AgentRunStatus.FAILED,
                    AgentRunStatus.REJECTED,
                    AgentRunStatus.CANCELLED,
                }:
                    yield "data: [DONE]\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/querymind/v1/agent-runs/{run_id}/cancel")
    async def cancel_agent_run(
        run_id: str,
        payload: CancelAgentRunRequest,
        request: Request,
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        try:
            run = await _store().transition_run(
                run_id,
                AgentRunStatus.CANCELLED,
                tenant_id=_tenant_id(user),
                user_id=user.id,
                stage="cancelled",
                expected_version=payload.expected_version,
                event_type="run.cancelled",
            )
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Agent run not found") from exc
        except (AgentRunTransitionError, AgentRunVersionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if executor is not None:
            await executor.cancel(run_id)
        return {"run": _run_response(run)}

    @app.post("/api/querymind/v1/agent-runs/{run_id}/approvals")
    async def decide_agent_run_approval(
        run_id: str,
        payload: ApprovalDecisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        tenant_id = _tenant_id(user)
        try:
            run_input = await _store().get_run_input(
                run_id,
                tenant_id=tenant_id,
                user_id=user.id,
            )
            run_input.operator_decisions.append(
                {
                    "decision": payload.decision,
                    "reason": payload.reason,
                    "decided_by": user.id,
                }
            )
            if payload.decision == "reject":
                run_input.gate_resolved = "rejected"
                run = await _store().resolve_gate(
                    run_id,
                    AgentRunStatus.REJECTED,
                    run_input,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    stage="approval.rejected",
                    expected_version=payload.expected_version,
                    event_type="approval.rejected",
                    event_data={"reason_provided": bool(payload.reason)},
                )
            else:
                run_input.gate_resolved = "approval"
                run = await _store().resolve_gate(
                    run_id,
                    AgentRunStatus.RUNNING,
                    run_input,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    stage="approval.approved",
                    expected_version=payload.expected_version,
                    event_type="approval.approved",
                    event_data={"reason_provided": bool(payload.reason)},
                )
                if executor is not None:
                    executor.submit(run_id, tenant_id=tenant_id, user_id=user.id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Agent run not found") from exc
        except (AgentRunTransitionError, AgentRunVersionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run": _run_response(run)}

    @app.post("/api/querymind/v1/agent-runs/{run_id}/clarifications")
    async def clarify_agent_run(
        run_id: str,
        payload: ClarificationRequest,
        request: Request,
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        tenant_id = _tenant_id(user)
        try:
            run_input = await _store().get_run_input(
                run_id,
                tenant_id=tenant_id,
                user_id=user.id,
            )
            run_input.clarification_answer = payload.answer
            run_input.gate_resolved = "clarification"
            run = await _store().resolve_gate(
                run_id,
                AgentRunStatus.RUNNING,
                run_input,
                tenant_id=tenant_id,
                user_id=user.id,
                stage="clarification.received",
                expected_version=payload.expected_version,
                event_type="clarification.received",
                event_data={"answer_received": True},
            )
            if executor is not None:
                executor.submit(run_id, tenant_id=tenant_id, user_id=user.id)
        except AgentRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Agent run not found") from exc
        except (AgentRunTransitionError, AgentRunVersionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run": _run_response(run)}

    @app.post("/api/querymind/v1/agent-runs/{run_id}/feedback", status_code=202)
    async def create_agent_run_feedback(
        run_id: str,
        payload: AgentRunFeedbackRequest,
        request: Request,
    ) -> dict[str, Any]:
        user = await _resolve_current_user(agent, request)
        tenant_id = _tenant_id(user)
        run = await _store().get_run(run_id, tenant_id=tenant_id, user_id=user.id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found")
        if run.status not in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.REJECTED,
        }:
            raise HTTPException(status_code=409, detail="Run is not ready for feedback")
        await _store().append_feedback(
            run_id,
            payload.model_dump(mode="json"),
            tenant_id=tenant_id,
            user_id=user.id,
        )
        event = await _store().append_event(
            run_id,
            "feedback.recorded",
            tenant_id=tenant_id,
            user_id=user.id,
            data={
                "rating": payload.rating,
                "reason_provided": bool(payload.reason),
            },
        )
        return {"event": event.model_dump(mode="json")}
