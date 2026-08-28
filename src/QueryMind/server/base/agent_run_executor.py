"""Background adapter that executes the existing Agent through durable Runs."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from QueryMind.core.agent_run import (
    AgentRunGateDecision,
    AgentRunStatus,
    AgentRunStore,
    AgentRunTransitionError,
    redact_question_preview,
)
from QueryMind.core.user import User

from .chat_handler import ChatHandler
from .models import ChatRequest

logger = logging.getLogger(__name__)

RunPolicy = Callable[
    [str, str, User],
    AgentRunGateDecision | Awaitable[AgentRunGateDecision],
]


def classify_run_exception(exc: Exception) -> tuple[str, str]:
    """Map runtime failures to stable, non-provider-specific error categories."""

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "provider_timeout", "provider_transient"
    if isinstance(exc, ConnectionError):
        return "provider_connection_error", "provider_transient"
    if isinstance(exc, PermissionError):
        return "permission_denied", "permission_denied"
    if isinstance(exc, ValueError):
        return "invalid_runtime_input", "provider_permanent"
    return type(exc).__name__, "agent_execution"


class AgentRunExecutor:
    """Own background tasks and map Chat Agent progress into durable events."""

    def __init__(
        self,
        store: AgentRunStore,
        chat_handler: ChatHandler,
        *,
        policy: RunPolicy | None = None,
    ) -> None:
        self.store = store
        self.chat_handler = chat_handler
        self.policy = policy
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    async def start(self) -> None:
        """Recover queued work and fail closed for interrupted running work."""

        self._closed = False
        for run in await self.store.list_active_runs():
            if run.status == AgentRunStatus.QUEUED:
                self.submit(run.id, tenant_id=run.tenant_id, user_id=run.user_id)
            elif run.status == AgentRunStatus.RUNNING:
                await self.store.transition_run(
                    run.id,
                    AgentRunStatus.FAILED,
                    tenant_id=run.tenant_id,
                    user_id=run.user_id,
                    stage="recovery.failed_closed",
                    event_type="run.recovery_failed_closed",
                    event_data={"reason_code": "execution_state_unknown"},
                    snapshot_updates={
                        "error_code": "execution_state_unknown",
                        "error_category": "restart_recovery",
                    },
                )

    async def stop(self) -> None:
        """Stop accepting work and cancel process-local tasks on shutdown."""

        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def submit(self, run_id: str, *, tenant_id: str, user_id: str) -> None:
        if self._closed:
            raise RuntimeError("Agent run executor is closed")
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._execute(run_id, tenant_id=tenant_id, user_id=user_id),
            name=f"agent-run:{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def cancel(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

    async def _assess(
        self,
        question: str,
        database_id: str,
        user: User,
    ) -> AgentRunGateDecision:
        if self.policy is None:
            return AgentRunGateDecision(action="allow")
        decision = self.policy(question, database_id, user)
        if inspect.isawaitable(decision):
            decision = await decision
        return AgentRunGateDecision.model_validate(decision)

    async def _execute(self, run_id: str, *, tenant_id: str, user_id: str) -> None:
        try:
            run = await self.store.get_run(run_id, tenant_id=tenant_id, user_id=user_id)
            if run is None or run.status not in {
                AgentRunStatus.QUEUED,
                AgentRunStatus.RUNNING,
            }:
                return
            run_input = await self.store.get_run_input(
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            user = User.model_validate(run_input.user or {"id": user_id})
            if run.status == AgentRunStatus.QUEUED:
                await self.store.transition_run(
                    run_id,
                    AgentRunStatus.RUNNING,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    stage="intent.classify",
                    event_type="run.started",
                )

            if run_input.gate_resolved is None:
                decision = await self._assess(run_input.question, run.database_id, user)
                if decision.action == "reject":
                    await self.store.transition_run(
                        run_id,
                        AgentRunStatus.REJECTED,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        stage="policy.rejected",
                        event_type="run.rejected",
                        event_data={"reason_code": decision.reason_code},
                        snapshot_updates={"risk_level": decision.risk_level},
                    )
                    return
                if decision.action in {"approve", "clarify"}:
                    target = (
                        AgentRunStatus.WAITING_FOR_APPROVAL
                        if decision.action == "approve"
                        else AgentRunStatus.WAITING_FOR_CLARIFICATION
                    )
                    await self.store.transition_run(
                        run_id,
                        target,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        stage=f"{decision.action}.wait",
                        event_type=f"{decision.action}.requested",
                        event_data={
                            "reason_code": decision.reason_code,
                            "message": decision.message,
                        },
                        snapshot_updates={"risk_level": decision.risk_level},
                    )
                    return

            question = run_input.question
            if run_input.clarification_answer:
                question = f"{question}\n\n用户补充说明：{run_input.clarification_answer}"

            execution_failed = False
            successful_sql_seen = False
            latest_result_validation: dict[str, Any] | None = None

            async def event_sink(event_type: str, data: dict[str, Any]) -> None:
                nonlocal execution_failed, successful_sql_seen
                nonlocal latest_result_validation
                if event_type == "agent.failed":
                    execution_failed = True
                if (
                    event_type == "tool.completed"
                    and data.get("tool_name") == "run_sql"
                    and data.get("success") is True
                ):
                    successful_sql_seen = True
                    metadata = data.get("metadata")
                    validation = (
                        metadata.get("result_validation")
                        if isinstance(metadata, dict)
                        else None
                    )
                    latest_result_validation = (
                        dict(validation) if isinstance(validation, dict) else None
                    )
                await self.store.append_event(
                    run_id,
                    event_type,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    data=data,
                )

            chunks = 0
            chat_request = ChatRequest(
                message=question,
                conversation_id=run.conversation_id,
                request_id=run_id,
                metadata={"database_id": run.database_id},
                runtime={"event_sink": event_sink, "resolved_user": user},
            )
            async for _chunk in self.chat_handler.handle_stream(chat_request):
                chunks += 1

            if execution_failed:
                await self.store.transition_run(
                    run_id,
                    AgentRunStatus.FAILED,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    stage="agent.failed",
                    event_type="run.failed",
                    snapshot_updates={
                        "error_code": "agent_failed",
                        "error_category": "agent_execution",
                    },
                )
                return

            validation_status = str(
                (latest_result_validation or {}).get("status") or ""
            )
            if not successful_sql_seen or validation_status != "passed":
                validation_issues = list(
                    (latest_result_validation or {}).get("issues") or []
                )
                if not successful_sql_seen or latest_result_validation is None:
                    error_code = "result_validation_missing"
                elif validation_status == "inconclusive":
                    error_code = "result_validation_inconclusive"
                else:
                    error_code = "result_validation_failed"
                await self.store.append_event(
                    run_id,
                    "result.validation_failed",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    data={
                        "status": validation_status or "missing",
                        "issues": validation_issues,
                    },
                )
                await self.store.transition_run(
                    run_id,
                    AgentRunStatus.FAILED,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    stage="result.validation_failed",
                    event_type="run.failed",
                    event_data={"reason_code": error_code},
                    snapshot_updates={
                        "error_code": error_code,
                        "error_category": "result_validation",
                    },
                )
                return

            await self.store.append_event(
                run_id,
                "result.validated",
                tenant_id=tenant_id,
                user_id=user_id,
                data={
                    "status": "passed",
                    "component_count": chunks,
                    "evidence": dict(
                        (latest_result_validation or {}).get("evidence") or {}
                    ),
                },
            )
            await self.store.transition_run(
                run_id,
                AgentRunStatus.SUCCEEDED,
                tenant_id=tenant_id,
                user_id=user_id,
                stage="answer.complete",
                event_type="run.succeeded",
                event_data={"component_count": chunks},
                snapshot_updates={
                    "result_summary_redacted": (
                        f"Completed with {chunks} response components"
                    )
                },
            )
        except asyncio.CancelledError:
            logger.info("Agent run task cancelled: %s", run_id)
            raise
        except AgentRunTransitionError:
            logger.info("Agent run changed state before executor completion: %s", run_id)
        except Exception as exc:
            logger.exception("Agent run execution failed: %s", run_id)
            try:
                error_code, error_category = classify_run_exception(exc)
                current = await self.store.get_run(
                    run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if current is not None and current.status == AgentRunStatus.RUNNING:
                    await self.store.transition_run(
                        run_id,
                        AgentRunStatus.FAILED,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        stage="run.failed",
                        event_type="run.failed",
                        event_data={
                            "exception_type": type(exc).__name__,
                            "message": redact_question_preview(
                                str(exc),
                                max_length=500,
                            ),
                        },
                        snapshot_updates={
                            "error_code": error_code,
                            "error_category": error_category,
                        },
                    )
            except Exception:
                logger.exception("Failed to persist Agent run failure: %s", run_id)
