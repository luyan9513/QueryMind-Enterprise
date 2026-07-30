from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.capabilities.agent_memory import AgentMemory  # noqa: E402
from QueryMind.capabilities.sql_runner import RunSqlToolArgs  # noqa: E402
from QueryMind.core.agent.agent import (  # noqa: E402
    _append_metadata_recovery_prompt,
    _compose_final_response_content,
    _is_rejected_metadata_sql,
    _metadata_recovery_tools,
)
from QueryMind.core.agent.config import AgentConfig  # noqa: E402
from QueryMind.core.system_prompt.default import DefaultSystemPromptBuilder  # noqa: E402
from QueryMind.core.tool import ToolCall, ToolContext, ToolResult  # noqa: E402
from QueryMind.core.tool import ToolSchema  # noqa: E402
from QueryMind.core.user import User  # noqa: E402
from QueryMind.runtime_paths import repo_root  # noqa: E402
from QueryMind.tools.run_sql import RunSqlTool  # noqa: E402


class DummyAgentMemory(AgentMemory):
    async def save_tool_usage(self, *args, **kwargs):
        return None

    async def save_text_memory(self, *args, **kwargs):
        return None

    async def search_similar_usage(self, *args, **kwargs):
        return []

    async def search_text_memories(self, *args, **kwargs):
        return []

    async def get_recent_memories(self, *args, **kwargs):
        return []

    async def get_recent_text_memories(self, *args, **kwargs):
        return []

    async def delete_by_id(self, *args, **kwargs):
        return False

    async def delete_text_memory(self, *args, **kwargs):
        return False

    async def clear_memories(self, *args, **kwargs):
        return 0


class DummySqlRunner:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.calls = []

    async def run_sql(self, args, context):
        self.calls.append(args.sql)
        return self.df


class DummyFileSystem:
    def __init__(self):
        self.writes = []

    async def write_file(self, filename, content, context, overwrite=True):
        self.writes.append(
            {
                "filename": filename,
                "content": content,
                "overwrite": overwrite,
            }
        )


def test_run_sql_defaults_to_repo_query_results_directory() -> None:
    tool = RunSqlTool(sql_runner=DummySqlRunner(pd.DataFrame({"id": [1]})))

    assert tool.file_system.working_directory == repo_root() / "query_results"


def _make_context() -> ToolContext:
    return ToolContext(
        user=User(
            id="u1",
            username="tester",
            email="tester@example.com",
            group_memberships=[],
        ),
        conversation_id="conv-1",
        request_id="req-1",
        agent_memory=DummyAgentMemory(),
    )


def test_run_sql_metadata_includes_executed_sql_for_select() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"id": [1, 2]})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(_make_context(), RunSqlToolArgs(sql="SELECT * FROM demo"))
    )

    assert result.success is True
    assert result.metadata["executed_sql"] == "SELECT * FROM demo"


def test_run_sql_metadata_includes_executed_sql_for_write_query() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"rows_affected": [1]})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(_make_context(), RunSqlToolArgs(sql="UPDATE demo SET x = 1"))
    )

    assert result.success is True
    assert result.metadata["executed_sql"] == "UPDATE demo SET x = 1"


def test_compose_final_response_appends_last_successful_sql_once() -> None:
    tool_results = [
        {
            "tool_name": "run_sql",
            "success": False,
            "metadata": {"executed_sql": "SELECT 1"},
        },
        {
            "tool_name": "visualize_data",
            "success": True,
            "metadata": {"actual_chart_type": "pie"},
        },
        {
            "tool_name": "run_sql",
            "success": True,
            "metadata": {"executed_sql": "SELECT * FROM sales"},
        },
    ]

    composed = _compose_final_response_content("Here is the result.", tool_results)

    assert composed.startswith("Here is the result.")
    assert composed.count("```sql") == 1
    assert "SELECT * FROM sales" in composed


def test_compose_final_response_does_not_duplicate_existing_sql() -> None:
    tool_results = [
        {
            "tool_name": "run_sql",
            "success": True,
            "metadata": {"executed_sql": "SELECT * FROM sales"},
        }
    ]
    response_content = "Here is the SQL:\n\n```sql\nSELECT * FROM sales\n```"

    composed = _compose_final_response_content(response_content, tool_results)

    assert composed == response_content


def test_system_prompt_mentions_sql_fallback() -> None:
    prompt = asyncio.run(
        DefaultSystemPromptBuilder().build_system_prompt(
        user=User(
            id="u1",
            username="tester",
            email="tester@example.com",
            group_memberships=[],
        ),
        tools=[
            ToolSchema(name="run_sql", description="Execute SQL", parameters={}),
            ToolSchema(
                name="submit_query_plan",
                description="Submit plan",
                parameters={},
            ),
        ],
    )
    )

    assert "append the executed SQL" in prompt
    assert "Return exactly the dimensions and metrics" in prompt
    assert "Preserve database numeric precision" in prompt
    assert "Do not invent status" in prompt
    assert "submit_query_plan" in prompt
    assert "accepted plan exactly" in prompt
    assert "Runtime context notices are authoritative" in prompt


def test_metadata_query_recovery_is_narrow_and_schema_only() -> None:
    rejected = ToolResult(
        success=False,
        result_for_llm="rejected",
        error="rejected",
        metadata={"rejection_stage": "governance"},
    )
    metadata_call = ToolCall(
        id="call-1",
        name="run_sql",
        arguments={"sql": "SELECT * FROM information_schema.columns"},
    )
    business_call = metadata_call.model_copy(
        update={"arguments": {"sql": "SELECT * FROM sales.orders"}}
    )
    tools = [
        ToolSchema(name="run_sql", description="sql", parameters={}),
        ToolSchema(name="schema_retrieve", description="schema", parameters={}),
    ]

    assert _is_rejected_metadata_sql(metadata_call, rejected) is True
    assert _is_rejected_metadata_sql(business_call, rejected) is False
    assert [tool.name for tool in _metadata_recovery_tools(tools)] == [
        "schema_retrieve"
    ]
    prompt = _append_metadata_recovery_prompt("base")
    assert "run_sql` is temporarily unavailable" in prompt
    assert "required_fields" in prompt
    exhausted_prompt = _append_metadata_recovery_prompt("base", exhausted=True)
    assert "Do not call tools again" in exhausted_prompt
    assert "ask the user" in exhausted_prompt

    planning_rejected = rejected.model_copy(
        update={"metadata": {"rejection_stage": "planning"}}
    )
    assert _is_rejected_metadata_sql(metadata_call, planning_rejected) is True


def test_agent_metadata_retry_limit_is_bounded() -> None:
    assert AgentConfig().max_metadata_query_retries == 2

    try:
        AgentConfig(max_metadata_query_retries=0)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("zero metadata retry limit should fail validation")


def test_agent_query_plan_defaults_are_bounded_and_opt_in() -> None:
    config = AgentConfig()

    assert config.require_query_plan is False
    assert config.max_query_plan_retries == 3

    try:
        AgentConfig(max_query_plan_retries=0)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("zero query plan retry limit should fail validation")
