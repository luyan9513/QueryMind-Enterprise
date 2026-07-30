"""Independent LLM review tool for high-risk SQL business semantics."""

from __future__ import annotations

import json
from typing import Type

from pydantic import BaseModel, Field

from QueryMind.core.agent.sql_review import (
    SqlIntentReview,
    parse_sql_intent_review,
    sql_fingerprint,
)
from QueryMind.core.llm import LlmMessage, LlmRequest, LlmService
from QueryMind.core.tool import Tool, ToolContext, ToolResult


class ReviewSqlIntentArgs(BaseModel):
    sql: str = Field(min_length=1, description="Candidate read-only SQL to review")
    intent_summary: str = Field(
        min_length=1,
        description="Plain-language metric, row grain, filters, and output contract",
    )
    assumptions: list[str] = Field(default_factory=list)


_REVIEW_SYSTEM_PROMPT = """
You are an independent pre-execution reviewer for enterprise Text2SQL. Review
only the supplied user question, runtime schema evidence, accepted query plan,
intent summary, assumptions, and candidate SQL. Never invent tables, columns,
or business definitions. Check metric formula, row grain, DISTINCT need, join
cardinality, filters and time boundaries, null handling, grouping, ordering,
limit, and exact output shape. Approve unless there is a clear mismatch. Use
clarify when the business definition is genuinely unsupported or ambiguous.
Return JSON only with: decision (approve|revise|clarify), confidence (0..1),
risk_tags (array), feedback (short actionable text), checked_dimensions (array).
""".strip()


class ReviewSqlIntentTool(Tool[ReviewSqlIntentArgs]):
    def __init__(self, llm_service: LlmService) -> None:
        self.llm_service = llm_service

    @property
    def name(self) -> str:
        return "review_sql_intent"

    @property
    def description(self) -> str:
        return (
            "Independently review high-risk SQL against the user's business intent, "
            "retrieved schema evidence, and accepted query plan before run_sql."
        )

    def get_args_schema(self) -> Type[ReviewSqlIntentArgs]:
        return ReviewSqlIntentArgs

    async def execute(
        self,
        context: ToolContext,
        args: ReviewSqlIntentArgs,
    ) -> ToolResult:
        evidence = context.metadata.get("schema_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        payload = {
            "user_question": context.raw_user_message or "",
            "intent_summary": args.intent_summary,
            "assumptions": list(args.assumptions),
            "accepted_query_plan": context.metadata.get("query_plan"),
            "schema_evidence": {
                "tables": list(evidence.get("tables") or []),
                "table_columns": dict(evidence.get("table_columns") or {}),
                "table_primary_keys": dict(evidence.get("table_primary_keys") or {}),
            },
            "candidate_sql": args.sql,
            "dialect": context.metadata.get("dialect"),
        }
        response = await self.llm_service.send_request(
            LlmRequest(
                messages=[
                    LlmMessage(
                        role="user",
                        content=json.dumps(payload, ensure_ascii=False),
                    )
                ],
                tools=None,
                user=context.user,
                stream=False,
                temperature=0.0,
                max_tokens=600,
                system_prompt=_REVIEW_SYSTEM_PROMPT,
                metadata={
                    "conversation_id": context.conversation_id,
                    "request_id": context.request_id,
                    "purpose": "sql_intent_review",
                },
            )
        )
        try:
            review = parse_sql_intent_review(response.content or "")
        except Exception as exc:
            review = SqlIntentReview(
                decision="clarify",
                confidence=0.0,
                risk_tags=["review_parse_failure"],
                feedback=(
                    "The semantic reviewer returned an invalid response; do not execute "
                    "the high-risk SQL automatically."
                ),
                checked_dimensions=[],
            )
            parse_error = str(exc)
        else:
            parse_error = ""

        snapshot = {
            "status": "approved" if review.approved else review.decision,
            "approved": review.approved,
            "sql_fingerprint": sql_fingerprint(args.sql),
            "decision": review.decision,
            "confidence": review.confidence,
            "risk_tags": list(review.risk_tags),
            "feedback": review.feedback,
            "checked_dimensions": list(review.checked_dimensions),
        }
        context.metadata["sql_intent_review"] = snapshot
        text = (
            "SQL semantic review approved. Proceed with this exact SQL."
            if review.approved
            else (
                f"SQL semantic review requires {review.decision}: {review.feedback} "
                "Revise the intent, plan, or SQL and review again before run_sql."
            )
        )
        return ToolResult(
            success=True,
            result_for_llm=text,
            metadata={
                "tool_name": self.name,
                "sql_intent_review": snapshot,
                "review_llm_usage": dict(response.usage or {}),
                "review_llm_model": getattr(self.llm_service, "model", "unknown"),
                "review_parse_error": parse_error,
            },
        )
