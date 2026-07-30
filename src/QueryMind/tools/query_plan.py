"""Tool for submitting a schema-grounded query plan before run_sql."""

from __future__ import annotations

from typing import Type

from QueryMind.core.agent.query_plan import (
    QueryPlan,
    validate_query_plan_evidence,
    validate_query_plan_intent,
)
from QueryMind.core.tool import Tool, ToolContext, ToolResult


class SubmitQueryPlanTool(Tool[QueryPlan]):
    """Persist a validated, turn-local plan for SQL alignment checks."""

    @property
    def name(self) -> str:
        return "submit_query_plan"

    @property
    def description(self) -> str:
        return (
            "Submit the exact query plan after schema retrieval and before run_sql. "
            "Cite only retrieved physical tables and qualified columns. The plan "
            "must define row grain, exact output columns, filters, aggregation, "
            "grouping, ordering, and unresolved business questions."
        )

    def get_args_schema(self) -> Type[QueryPlan]:
        return QueryPlan

    async def execute(self, context: ToolContext, args: QueryPlan) -> ToolResult:
        context.metadata.pop("sql_intent_review", None)
        check = validate_query_plan_evidence(args, context.metadata)
        intent_check = validate_query_plan_intent(
            args,
            context.metadata,
            context.raw_user_message,
        )
        check.issues.extend(
            issue for issue in intent_check.issues if issue not in check.issues
        )
        check.evidence.update(intent_check.evidence)
        plan_data = args.model_dump(mode="json")
        accepted = check.passed
        status = "accepted" if accepted else "rejected"

        snapshot = {
            "query_plan": plan_data,
            "query_plan_status": status,
            "query_plan_issues": list(check.issues),
            "query_plan_evidence": dict(check.evidence),
        }
        context.metadata.update(snapshot)

        if accepted:
            output_names = ", ".join(args.output_columns)
            result_for_llm = (
                "Query plan accepted. Generate one SQL statement using only the "
                f"accepted tables and columns. Return exactly: {output_names}."
            )
            return ToolResult(
                success=True,
                result_for_llm=result_for_llm,
                metadata={"tool_name": self.name, **snapshot},
            )

        issue_text = "; ".join(check.issues[:8])
        return ToolResult(
            success=False,
            result_for_llm=(
                "Query plan rejected. Retrieve the missing schema evidence or ask "
                f"the user to resolve ambiguity, then submit a new plan: {issue_text}"
            ),
            error="Query plan is not grounded in available schema evidence",
            metadata={
                "tool_name": self.name,
                "rejection_stage": "planning",
                "rejection_code": "query_plan_evidence_gap",
                **snapshot,
            },
        )
