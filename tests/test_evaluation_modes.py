from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.core.agent import AgentConfig  # noqa: E402
from QueryMind.core.evaluation import (  # noqa: E402
    EvaluationConversationStore,
    EvaluationMode,
    EvaluationRunner,
    EvaluationRuntime,
    parse_evaluation_mode,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory  # noqa: E402
from QueryMind.core.evaluation.single_shot import (  # noqa: E402
    SingleShotEvaluationAgent,
    extract_single_shot_sql,
)
from QueryMind.core.llm import (  # noqa: E402
    LlmRequest,
    LlmResponse,
    LlmService,
    LlmStreamChunk,
)
from QueryMind.core.tool import ToolCall, ToolResult, ToolSchema  # noqa: E402
from QueryMind.core.user import RequestContext, User  # noqa: E402


class _SingleResponseLlm(LlmService):
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[LlmRequest] = []

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            content=self.content,
            usage={"prompt_tokens": 30, "completion_tokens": 10},
        )

    async def stream_request(
        self,
        request: LlmRequest,
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        if False:  # pragma: no cover - required async-generator shape
            yield LlmStreamChunk()

    async def validate_tools(self, tools: List[ToolSchema]) -> List[str]:
        return []


class _RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def execute(self, tool_call: ToolCall, context) -> ToolResult:
        self.calls.append(tool_call)
        if tool_call.name == "schema_retrieve":
            return ToolResult(
                success=True,
                result_for_llm="Table: sales.orders; fields: id, total",
                metadata={
                    "tool_name": "schema_retrieve",
                    "selected_tables": ["sales.orders"],
                },
            )
        return ToolResult(
            success=True,
            result_for_llm="id,total\n1,10",
            metadata={
                "tool_name": "run_sql",
                "executed_sql": tool_call.arguments["sql"],
            },
        )


def test_parse_evaluation_mode_accepts_stable_aliases() -> None:
    assert parse_evaluation_mode("s0") == EvaluationMode.S0_SINGLE_SHOT
    assert parse_evaluation_mode("agent-without-plan") == (
        EvaluationMode.S1_AGENT_WITHOUT_PLAN
    )
    assert parse_evaluation_mode(None) == EvaluationMode.S2_AGENT_WITH_PLAN

    with pytest.raises(ValueError, match="s0, s1, s2"):
        parse_evaluation_mode("unknown")


@pytest.mark.asyncio
async def test_single_shot_uses_one_retrieval_one_llm_and_one_sql_attempt() -> None:
    llm = _SingleResponseLlm('{"sql":"SELECT id, total FROM sales.orders"}')
    registry = _RecordingRegistry()
    store = EvaluationConversationStore()
    user = User(
        id="evaluation",
        username="evaluation",
        email="evaluation@example.com",
        group_memberships=["admin"],
    )
    agent = SingleShotEvaluationAgent(
        llm_service=llm,
        tool_registry=registry,
        conversation_store=store,
        user=user,
        agent_memory=NoOpAgentMemory(),
        config=AgentConfig(stream_responses=False, temperature=0.0),
        dialect="postgres",
    )

    components = [
        component
        async for component in agent.send_message(
            RequestContext(metadata={"evaluation": True}),
            "列出订单金额",
            conversation_id="eval-s0",
        )
    ]

    assert components
    assert [call.name for call in registry.calls] == ["schema_retrieve", "run_sql"]
    assert len(llm.requests) == 1
    assert llm.requests[0].tools is None
    assert "ground_truth" not in llm.requests[0].messages[0].content

    conversation = await store.get_conversation("eval-s0", user)
    assert conversation is not None
    runner = EvaluationRunner(evaluators=[], runtime_resolver=None)  # type: ignore[arg-type]
    records, final_answer, usage, llm_calls = runner._extract_trace(conversation)
    assert [record.tool_name for record in records] == ["schema_retrieve", "run_sql"]
    assert all(record.success for record in records)
    assert "SELECT id, total" in (final_answer or "")
    assert usage == {"prompt_tokens": 30, "completion_tokens": 10}
    assert llm_calls == 1


def test_extract_single_shot_sql_supports_json_and_fenced_sql() -> None:
    assert extract_single_shot_sql('{"sql":"SELECT 1"}') == "SELECT 1"
    assert extract_single_shot_sql("```sql\nSELECT 1\n```") == "SELECT 1;"

    with pytest.raises(ValueError, match="non-empty 'sql'"):
        extract_single_shot_sql("I cannot answer")


@pytest.mark.asyncio
async def test_query_plan_tool_is_only_registered_for_s2() -> None:
    llm = _SingleResponseLlm('{"sql":"SELECT 1"}')
    runtime = EvaluationRuntime(
        database_id="demo",
        dialect="postgres",
        sql_runner=object(),  # type: ignore[arg-type]
        schema_extractor=None,
        agent_llm_service=llm,
        schema_memory=object(),
    )

    runtime.agent_config.require_query_plan = False
    s1_tools = await runtime._build_tool_registry().list_tools()
    runtime.agent_config.require_query_plan = True
    s2_tools = await runtime._build_tool_registry().list_tools()

    assert "submit_query_plan" not in s1_tools
    assert "submit_query_plan" in s2_tools
