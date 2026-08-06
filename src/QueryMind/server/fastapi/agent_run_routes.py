"""Versioned Agent run lifecycle routes."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from QueryMind.core.agent_run import (
    AgentRunIdempotencyConflictError,
    AgentRunNotFoundError,
    AgentRunStatus,
    AgentRunStore,
    AgentRunTransitionError,
    AgentRunVersionConflictError,
    redact_question_preview,
)
from QueryMind.core.user import User
from QueryMind.core.user.request_context import RequestContext


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
) -> None:
    """Register the v0.10-A lifecycle API without changing chat execution."""

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
            )
        except AgentRunIdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
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
        return {"run": _run_response(run)}
