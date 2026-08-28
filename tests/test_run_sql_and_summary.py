from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
from QueryMind.core.tool import (  # noqa: E402
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSchema,  # noqa: E402
)
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


def _make_planned_context(**plan_updates) -> ToolContext:
    context = _make_context()
    plan = {
        "objective": "List demo rows",
        "source_tables": ["public.demo"],
        "required_columns": ["public.demo.id", "public.demo.name"],
        "grain_keys": ["public.demo.id"],
        "row_grain": "one row per demo",
        "output_columns": ["id", "name"],
    }
    plan.update(plan_updates)
    context.metadata.update(
        {
            "query_plan": plan,
            "query_plan_status": "accepted",
            "semantic_contract_validation": {
                "mode": "required",
                "passed": True,
                "issues": [],
            },
        }
    )
    return context


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


def test_run_sql_marks_plan_aligned_non_empty_result_as_validated() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(),
            RunSqlToolArgs(sql="SELECT id, name FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "passed"
    assert result.metadata["result_validation"]["issues"] == []


def test_run_sql_treats_plan_label_and_sql_alias_separators_as_equivalent() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"artist_name": ["a"], "total_revenue": [1]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(
                output_columns=["artist name", "total revenue"],
                grain_keys=["artist name"],
            ),
            RunSqlToolArgs(
                sql="SELECT artist_name, total_revenue FROM demo"
            ),
        )
    )

    assert result.metadata["result_validation"]["status"] == "passed"
    assert result.metadata["result_validation"]["issues"] == []


def test_run_sql_accepts_machine_name_with_parenthesized_display_alias() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"账单编号": [1], "金额": [2]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(
                output_columns=["invoice_id（账单编号）", "total (金额)"],
                grain_keys=["invoice_id（账单编号）"],
            ),
            RunSqlToolArgs(sql="SELECT invoice_id AS 账单编号, total AS 金额 FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "passed"
    assert result.metadata["result_validation"]["issues"] == []


def test_run_sql_accepts_label_before_parenthesized_formula() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"销售数量": [2]})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(
                output_columns=["销售数量 (SUM(quantity))"],
                grain_keys=[],
                required_columns=["public.demo.quantity"],
            ),
            RunSqlToolArgs(sql="SELECT SUM(quantity) AS 销售数量 FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "passed"
    assert result.metadata["result_validation"]["issues"] == []


def test_run_sql_does_not_validate_output_column_drift() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"id": [1], "display_name": ["a"]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(),
            RunSqlToolArgs(sql="SELECT id, name AS display_name FROM demo"),
        )
    )

    validation = result.metadata["result_validation"]
    assert validation["status"] == "failed"
    assert "output_column_mismatch:2:name!=display_name" in validation["issues"]
    assert "must not be treated as validated" in result.result_for_llm


def test_run_sql_does_not_validate_duplicate_output_grain() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"id": [1, 1], "name": ["a", "a"]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(),
            RunSqlToolArgs(sql="SELECT id, name FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "failed"
    assert "duplicate_result_grain:id" in result.metadata["result_validation"][
        "issues"
    ]


def test_run_sql_does_not_validate_rows_beyond_planned_limit() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(limit=1),
            RunSqlToolArgs(sql="SELECT id, name FROM demo LIMIT 1"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "failed"
    assert "row_limit_exceeded:2>1" in result.metadata["result_validation"][
        "issues"
    ]


def test_run_sql_does_not_validate_multirow_scalar_aggregate() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(
            pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
        ),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(
                requires_aggregation=True,
                requires_grouping=False,
            ),
            RunSqlToolArgs(sql="SELECT count(*) AS id, max(name) AS name FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "failed"
    assert "aggregate_row_count_mismatch:expected=1:actual=2" in result.metadata[
        "result_validation"
    ]["issues"]


def test_run_sql_requires_successful_semantic_contract_validation() -> None:
    context = _make_planned_context()
    context.metadata["semantic_contract_validation"]["passed"] = False
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"id": [1], "name": ["a"]})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            context,
            RunSqlToolArgs(sql="SELECT id, name FROM demo"),
        )
    )

    assert result.metadata["result_validation"]["status"] == "failed"
    assert "semantic_contract_not_validated" in result.metadata[
        "result_validation"
    ]["issues"]


def test_run_sql_zero_rows_returns_validation_notice() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"id": []})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(_make_context(), RunSqlToolArgs(sql="SELECT * FROM demo"))
    )

    assert result.success is True
    assert result.metadata["row_count"] == 0
    assert "returned zero rows" in result.result_for_llm
    assert "every filter was supported" in result.result_for_llm


def test_run_sql_zero_rows_remain_inconclusive_even_with_a_plan() -> None:
    tool = RunSqlTool(
        sql_runner=DummySqlRunner(pd.DataFrame({"id": [], "name": []})),
        file_system=DummyFileSystem(),
    )

    result = asyncio.run(
        tool.execute(
            _make_planned_context(),
            RunSqlToolArgs(sql="SELECT id, name FROM demo"),
        )
    )

    validation = result.metadata["result_validation"]
    assert validation["status"] == "inconclusive"
    assert validation["issues"] == ["unexpected_zero_rows"]


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
    assert "Conditional aggregates" in prompt
    assert "unexpected zero-row result" in prompt
    assert "schema relationships" in prompt
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
