"""Tool for submitting a schema-grounded query plan before run_sql."""

from __future__ import annotations

from typing import Type

from QueryMind.core.agent.query_plan import (
    QueryPlan,
    validate_query_plan_evidence,
    validate_query_plan_intent,
)
from QueryMind.core.agent.semantic_contract import (
    SemanticContractMode,
    parse_semantic_contract_mode,
    validate_query_plan_semantic_contracts,
)
from QueryMind.core.tool import Tool, ToolContext, ToolResult


class SubmitQueryPlanTool(Tool[QueryPlan]):
    """Persist a validated, turn-local plan for SQL alignment checks."""

    def __init__(
        self,
        *,
        semantic_contract_mode: str | SemanticContractMode = "disabled",
    ) -> None:
        self.semantic_contract_mode = parse_semantic_contract_mode(
            semantic_contract_mode
        )

    @property
    def name(self) -> str:
        return "submit_query_plan"

    @property
    def description(self) -> str:
        return (
            "Submit the exact query plan after schema retrieval and before run_sql. "
            "Cite only retrieved physical tables and qualified columns. The plan "
            "must define row grain, exact output columns, filters, aggregation, "
            "grouping, ordering, semantic contract IDs, and unresolved questions."
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
        contract_check = validate_query_plan_semantic_contracts(
            args,
            context.metadata,
            mode=self.semantic_contract_mode,
            dialect=str(context.metadata.get("dialect") or "").strip() or None,
        )
        if self.semantic_contract_mode == SemanticContractMode.REQUIRED:
            check.issues.extend(
                issue for issue in contract_check.issues if issue not in check.issues
            )
        check.evidence["semantic_contract"] = dict(contract_check.evidence)
        check.evidence["semantic_contract"].update(
            {
                "passed": contract_check.passed,
                "issues": list(contract_check.issues),
            }
        )
        if contract_check.advisories:
            check.evidence["semantic_contract"]["advisories"] = list(
                contract_check.advisories
            )
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
            error="Query plan is not grounded in schema and semantic evidence",
            metadata={
                "tool_name": self.name,
                "rejection_stage": "planning",
                "rejection_code": "query_plan_evidence_gap",
                **snapshot,
            },
        )
