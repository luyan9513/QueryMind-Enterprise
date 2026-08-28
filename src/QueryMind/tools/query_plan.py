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


def _repair_hints(issues: list[str]) -> list[str]:
    """Turn deterministic plan issues into concise, actionable repair steps."""
    hints: list[str] = []
    for issue in issues:
        if issue.startswith("joined_primary_key_count_requires_distinct:"):
            column = issue.split(":", 1)[1]
            hints.append(
                f"replace COUNT({column}) with COUNT(DISTINCT {column})"
            )
        elif issue.startswith(
            "grouped_entity_primary_key_missing_from_grain_keys:"
        ):
            column = issue.split(":", 1)[1]
            hints.append(f"add {column} to grain_keys")
        elif issue.startswith(
            "grouped_entity_primary_key_missing_from_required_columns:"
        ):
            column = issue.split(":", 1)[1]
            hints.append(f"add {column} to required_columns")
        elif issue.startswith("grain_key_conflicts_with_row_grain:"):
            column = issue.split(":", 1)[1]
            hints.append(
                f"remove {column} from grain_keys; grain_keys must identify the "
                "final result row, not a joined detail row"
            )
        elif issue.startswith("unrequested_distinct_primary_key_count:"):
            column = issue.split(":", 1)[1]
            hints.append(
                f"use COUNT(*) instead of COUNT(DISTINCT {column}) for this "
                "single-table row count"
            )
        elif issue.startswith("top_per_group_limit_mismatch:"):
            expected = issue.split("expected=", 1)[1].split(":", 1)[0]
            hints.append(
                f"set partition_limit to {expected} and plan a window ranking "
                f"followed by a filter that retains {expected} row(s) per group"
            )
    return list(dict.fromkeys(hints))


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
            "grouping, stable grain keys, ordering, semantic contract IDs, and "
            "unresolved questions."
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
        repair_hints = _repair_hints(check.issues)
        repair_text = (
            " Required correction: " + "; ".join(repair_hints[:4]) + "."
            if repair_hints
            else ""
        )
        rejection_message = (
            "Query plan rejected. Retrieve the missing schema evidence or ask "
            "the user to resolve ambiguity, then submit a corrected plan. "
            f"Issues: {issue_text}.{repair_text} Do not resubmit an unchanged plan."
        )
        return ToolResult(
            success=False,
            result_for_llm=rejection_message,
            error=rejection_message,
            metadata={
                "tool_name": self.name,
                "rejection_stage": "planning",
                "rejection_code": "query_plan_evidence_gap",
                "query_plan_repair_hints": repair_hints,
                **snapshot,
            },
        )
