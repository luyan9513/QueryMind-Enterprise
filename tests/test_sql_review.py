import asyncio
from collections.abc import AsyncGenerator

from QueryMind.capabilities.sql_runner import RunSqlToolArgs
from QueryMind.core.agent import SqlReviewMode
from QueryMind.core.agent.agent import _apply_sql_review_tool_gate
from QueryMind.core.agent.sql_review import (
    parse_sql_intent_review,
    requires_sql_review,
    sql_fingerprint,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory
from QueryMind.core.llm import LlmRequest, LlmResponse, LlmService, LlmStreamChunk
from QueryMind.core.tool import ToolContext, ToolRejection, ToolSchema
from QueryMind.core.user import User
from QueryMind.rls_registry import RLSToolRegistry
from QueryMind.tools import ReviewSqlIntentArgs, ReviewSqlIntentTool, RunSqlTool


class _ReviewerLlm(LlmService):
    async def send_request(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            content=(
                '{"decision":"revise","confidence":0.9,'
                '"risk_tags":["missing_distinct"],'
                '"feedback":"Count distinct customers.",'
                '"checked_dimensions":["metric","grain"]}'
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def stream_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        yield LlmStreamChunk()

    async def validate_tools(self, tools: list) -> list[str]:
        return []


class _NoSqlRunner:
    async def run_sql(self, *args, **kwargs):
        raise AssertionError("run_sql should not execute before semantic review")


def _context(metadata: dict | None = None) -> ToolContext:
    return ToolContext(
        user=User(
            id="u1",
            username="u1",
            email="u1@example.com",
            group_memberships=["user"],
        ),
        conversation_id="c1",
        request_id="r1",
        raw_user_message="How many distinct customers ordered?",
        agent_memory=NoOpAgentMemory(),
        metadata={
            "dialect": "postgres",
            "schema_evidence": {
                "tables": ["sales.orders", "sales.customers"],
                "table_columns": {},
                "table_primary_keys": {},
            },
            **(metadata or {}),
        },
    )


def test_sql_review_parser_and_risk_routing() -> None:
    parsed = parse_sql_intent_review(
        "```json\n"
        '{"decision":"approve","confidence":0.8,"risk_tags":[],'
        '"feedback":"ok","checked_dimensions":["grain"]}'
        "\n```"
    )
    assert parsed.approved is True
    assert requires_sql_review(
        "SELECT * FROM sales.orders",
        _context().metadata,
        mode=SqlReviewMode.HIGH_RISK,
        dialect="postgres",
    ) is False
    assert requires_sql_review(
        "SELECT * FROM sales.orders o JOIN sales.customers c USING (customer_id)",
        _context().metadata,
        mode=SqlReviewMode.HIGH_RISK,
        dialect="postgres",
    ) is True


def test_review_gate_hides_run_sql_after_plan_until_approval() -> None:
    schemas = [
        ToolSchema(name="run_sql", description="sql", parameters={}),
        ToolSchema(name="review_sql_intent", description="review", parameters={}),
    ]
    pending = _apply_sql_review_tool_gate(
        schemas,
        {"query_plan_status": "accepted"},
        SqlReviewMode.HIGH_RISK,
    )
    assert [tool.name for tool in pending] == ["review_sql_intent"]

    approved = _apply_sql_review_tool_gate(
        schemas,
        {
            "query_plan_status": "accepted",
            "sql_intent_review": {"approved": True},
        },
        SqlReviewMode.HIGH_RISK,
    )
    assert [tool.name for tool in approved] == ["run_sql", "review_sql_intent"]


def test_review_tool_records_revise_decision_and_usage() -> None:
    tool = ReviewSqlIntentTool(_ReviewerLlm())
    context = _context()
    result = asyncio.run(
        tool.execute(
            context,
            ReviewSqlIntentArgs(
                sql="SELECT COUNT(customer_id) FROM sales.orders",
                intent_summary="Count distinct customers",
            ),
        )
    )
    review = result.metadata["sql_intent_review"]
    assert result.success is True
    assert review["approved"] is False
    assert review["decision"] == "revise"
    assert result.metadata["review_llm_usage"]["prompt_tokens"] == 10
    assert context.metadata["sql_intent_review"] == review


def test_registry_requires_matching_review_for_high_risk_sql() -> None:
    registry = RLSToolRegistry(
        config_path="missing-rls-config.yaml",
        query_plan_mode="disabled",
        sql_review_mode="high_risk",
    )
    tool = RunSqlTool(sql_runner=_NoSqlRunner())
    sql = "SELECT * FROM sales.orders o JOIN sales.customers c USING (customer_id)"
    context = _context()

    missing = asyncio.run(
        registry.transform_args(
            tool=tool,
            args=RunSqlToolArgs(sql=sql),
            user=context.user,
            context=context,
        )
    )
    assert isinstance(missing, ToolRejection)
    assert missing.stage == "semantic_review"

    context.metadata["sql_intent_review"] = {
        "approved": True,
        "sql_fingerprint": sql_fingerprint(sql),
    }
    approved = asyncio.run(
        registry.transform_args(
            tool=tool,
            args=RunSqlToolArgs(sql=sql),
            user=context.user,
            context=context,
        )
    )
    assert isinstance(approved, RunSqlToolArgs)
