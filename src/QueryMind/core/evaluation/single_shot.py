"""No-Agent single-shot Text2SQL baseline used by evaluation runs."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncGenerator, Optional

from QueryMind.components import RichTextComponent, SimpleTextComponent, UiComponent
from QueryMind.core.agent import AgentConfig
from QueryMind.core.llm import LlmMessage, LlmRequest, LlmService
from QueryMind.core.storage import Conversation, ConversationStore, Message
from QueryMind.core.tool import ToolCall, ToolContext, ToolResult
from QueryMind.core.user import RequestContext, User

from .mode import EvaluationMode

_FENCED_SQL_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_single_shot_sql(content: Optional[str]) -> str:
    """Extract one SQL statement from the baseline model response."""
    text = str(content or "").strip()
    if not text:
        raise ValueError("Single-shot model returned empty content")

    candidates = [text]
    fenced = _FENCED_SQL_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    for candidate in candidates:
        cleaned = candidate.strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            sql = payload.get("sql")
            if isinstance(sql, str) and sql.strip():
                return sql.strip()
        if cleaned.upper().startswith(("SELECT ", "WITH ")):
            return cleaned.rstrip(";").strip() + ";"

    raise ValueError(
        "Single-shot model response must be JSON with a non-empty 'sql' field"
    )


class SingleShotEvaluationAgent:
    """Generate SQL once from one fixed Schema Memory retrieval.

    This deliberately does not use the upstream Agent Loop. SQL still passes
    through the same evaluation tool registry, so read-only, RLS, and SQL
    governance checks remain active.
    """

    def __init__(
        self,
        *,
        llm_service: LlmService,
        tool_registry: Any,
        conversation_store: ConversationStore,
        user: User,
        agent_memory: Any,
        config: AgentConfig,
        dialect: str,
        schema_memory: Any = None,
        schema_management_service: Any = None,
        observability_provider: Any = None,
    ) -> None:
        self.llm_service = llm_service
        self.tool_registry = tool_registry
        self.conversation_store = conversation_store
        self.user = user
        self.agent_memory = agent_memory
        self.config = config
        self.dialect = dialect
        self.schema_memory = schema_memory
        self.schema_management_service = schema_management_service
        self.observability_provider = observability_provider

    async def send_message(
        self,
        request_context: RequestContext,
        message: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[UiComponent, None]:
        conversation_id = conversation_id or str(uuid.uuid4())
        conversation = await self.conversation_store.get_conversation(
            conversation_id,
            self.user,
        )
        if conversation is None:
            conversation = Conversation(id=conversation_id, user=self.user, messages=[])
        conversation.add_message(Message(role="user", content=message))

        context = ToolContext(
            user=self.user,
            conversation_id=conversation_id,
            request_id=str(uuid.uuid4()),
            raw_user_message=message,
            agent_memory=self.agent_memory,
            metadata={
                **request_context.metadata,
                "evaluation_mode": EvaluationMode.S0_SINGLE_SHOT.value,
                "dialect": self.dialect,
                "tool_iterations": 0,
                "max_tool_iterations": 2,
            },
            observability_provider=self.observability_provider,
            schema_memory=self.schema_memory,
            schema_management_service=self.schema_management_service,
            schema_search_default_limit=self.config.schema_search_default_limit,
            schema_search_default_threshold=self.config.schema_search_default_threshold,
            schema_search_default_mode=self.config.schema_search_default_mode,
        )

        schema_call = ToolCall(
            id=str(uuid.uuid4()),
            name="schema_retrieve",
            arguments={
                "query": message,
                "search_mode": self.config.schema_search_default_mode,
                "limit": self.config.schema_search_default_limit,
                "similarity_threshold": self.config.schema_search_default_threshold,
            },
        )
        schema_result = await self.tool_registry.execute(schema_call, context)
        self._append_tool_exchange(conversation, schema_call, schema_result)
        if schema_result.ui_component is not None:
            yield schema_result.ui_component

        if not schema_result.success:
            final_text = "Single-shot schema retrieval failed; no SQL was generated."
            conversation.add_message(Message(role="assistant", content=final_text))
            await self.conversation_store.update_conversation(conversation)
            yield self._text_component(final_text)
            return

        response = await self.llm_service.send_request(
            LlmRequest(
                messages=[
                    LlmMessage(
                        role="user",
                        content=(
                            f"Business question:\n{message}\n\n"
                            f"Schema evidence:\n{schema_result.result_for_llm}"
                        ),
                    )
                ],
                tools=None,
                user=self.user,
                stream=False,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                system_prompt=self._system_prompt(),
                metadata={
                    "evaluation": True,
                    "evaluation_mode": EvaluationMode.S0_SINGLE_SHOT.value,
                },
            )
        )

        usage_metadata = {
            "llm_usage": dict(response.usage or {}),
            "evaluation_mode": EvaluationMode.S0_SINGLE_SHOT.value,
        }
        try:
            sql = extract_single_shot_sql(response.content)
        except ValueError as exc:
            final_text = str(exc)
            conversation.add_message(
                Message(role="assistant", content=final_text, metadata=usage_metadata)
            )
            await self.conversation_store.update_conversation(conversation)
            yield self._text_component(final_text)
            return

        sql_call = ToolCall(
            id=str(uuid.uuid4()),
            name="run_sql",
            arguments={"sql": sql},
        )
        conversation.add_message(
            Message(
                role="assistant",
                content="",
                tool_calls=[sql_call],
                metadata=usage_metadata,
            )
        )
        context.metadata["tool_iterations"] = 1
        sql_result = await self.tool_registry.execute(sql_call, context)
        self._append_tool_result(conversation, sql_call, sql_result)
        if sql_result.ui_component is not None:
            yield sql_result.ui_component

        if sql_result.success:
            final_text = f"Single-shot SQL executed successfully.\n\n```sql\n{sql}\n```"
        else:
            final_text = "Single-shot SQL was rejected or failed; no repair was attempted."
        conversation.add_message(Message(role="assistant", content=final_text))
        await self.conversation_store.update_conversation(conversation)
        yield self._text_component(final_text)

    def _system_prompt(self) -> str:
        return (
            "You are the no-Agent baseline for a Text2SQL evaluation. "
            f"Generate exactly one read-only {self.dialect} query using only the "
            "provided schema evidence. Do not query metadata catalogs. Do not guess "
            "missing business definitions. Return exactly one JSON object with the "
            "shape {\"sql\": \"SELECT ...\"}; no markdown or explanation."
        )

    @staticmethod
    def _append_tool_exchange(
        conversation: Conversation,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> None:
        conversation.add_message(
            Message(role="assistant", content="", tool_calls=[tool_call])
        )
        SingleShotEvaluationAgent._append_tool_result(
            conversation,
            tool_call,
            result,
        )

    @staticmethod
    def _append_tool_result(
        conversation: Conversation,
        tool_call: ToolCall,
        result: ToolResult,
    ) -> None:
        metadata = dict(result.metadata or {})
        metadata["tool_success"] = result.success
        if result.error:
            metadata["tool_error"] = result.error
        conversation.add_message(
            Message(
                role="tool",
                content=result.result_for_llm,
                tool_call_id=tool_call.id,
                metadata=metadata,
            )
        )

    @staticmethod
    def _text_component(text: str) -> UiComponent:
        return UiComponent(
            rich_component=RichTextComponent(content=text),
            simple_component=SimpleTextComponent(text=text),
        )


__all__ = ["SingleShotEvaluationAgent", "extract_single_shot_sql"]
