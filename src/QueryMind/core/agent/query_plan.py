"""Structured query planning and runtime SQL alignment checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlglot import exp, parse_one

from .._compat import StrEnum
from .sql_governance_shape import analyze_sql_shape

_IDENTIFIER_QUOTE_RE = re.compile(r"[`\"\[\]]")
_COUNT_DISTINCT_RE = re.compile(
    r"(?i)\bcount\s*\(\s*distinct\s+([a-z_][a-z0-9_.$`\"\[\]]*)"
)
_EXPLICIT_DISTINCT_REQUEST_RE = re.compile(
    r"(?i)(?:\bdistinct\b|\bunique\b|去重|唯一)"
)


class QueryPlanMode(StrEnum):
    """How the runtime decides whether a query plan is mandatory."""

    DISABLED = "disabled"
    ALWAYS = "always"
    ADAPTIVE = "adaptive"


def parse_query_plan_mode(
    value: str | QueryPlanMode | None,
    *,
    require_query_plan: bool = False,
) -> QueryPlanMode:
    """Resolve the new mode while preserving the legacy boolean switch."""
    if isinstance(value, QueryPlanMode):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "": QueryPlanMode.ALWAYS if require_query_plan else QueryPlanMode.DISABLED,
        "off": QueryPlanMode.DISABLED,
        "false": QueryPlanMode.DISABLED,
        "disabled": QueryPlanMode.DISABLED,
        "on": QueryPlanMode.ALWAYS,
        "true": QueryPlanMode.ALWAYS,
        "required": QueryPlanMode.ALWAYS,
        "always": QueryPlanMode.ALWAYS,
        "adaptive": QueryPlanMode.ADAPTIVE,
        "risk_based": QueryPlanMode.ADAPTIVE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            "Query plan mode must be one of: disabled, always, adaptive"
        ) from exc


class QueryPlanFilter(BaseModel):
    """A filter that must be visible in the generated SQL."""

    column: str = Field(
        min_length=1,
        description="Qualified schema.table.column or table.column from schema evidence",
    )
    operator: str = Field(
        min_length=1,
        description="SQL comparison such as =, >=, <, BETWEEN, IN, or IS NULL",
    )
    value_description: str = Field(
        min_length=1,
        description="User-requested value or a concise description of its source",
    )


class QueryPlan(BaseModel):
    """Database-grounded plan submitted before SQL execution."""

    objective: str = Field(min_length=1)
    source_tables: List[str] = Field(
        min_length=1,
        description="Physical tables used by the SQL, copied from schema evidence",
    )
    required_columns: List[str] = Field(
        min_length=1,
        description="Qualified columns required by metrics, joins, filters, and output",
    )
    metric_expressions: List[str] = Field(default_factory=list)
    dimensions: List[str] = Field(default_factory=list)
    filters: List[QueryPlanFilter] = Field(default_factory=list)
    row_grain: str = Field(
        min_length=1,
        description="What one result row represents",
    )
    output_columns: List[str] = Field(
        min_length=1,
        description="Exact user-facing output columns, in order",
    )
    join_path: List[str] = Field(default_factory=list)
    requires_aggregation: bool = False
    requires_grouping: bool = False
    requires_ordering: bool = False
    limit: Optional[int] = Field(default=None, gt=0)
    business_definition_notes: List[str] = Field(default_factory=list)
    semantic_contract_ids: List[str] = Field(
        default_factory=list,
        description="Approved data-source metric contract IDs used by this plan",
    )
    semantic_contract_version: Optional[str] = Field(
        default=None,
        description="Version of the matched data-source semantic contract catalog",
    )
    unresolved_questions: List[str] = Field(
        default_factory=list,
        description="Ambiguities that must be clarified before SQL execution",
    )


@dataclass(slots=True)
class QueryPlanCheck:
    """Machine-readable result of a plan or SQL check."""

    issues: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.issues


@dataclass(slots=True)
class QueryPlanRiskAssessment:
    """Deterministic SQL-shape decision used by adaptive plan routing."""

    requires_plan: bool
    risk_level: str
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)


def _identifier_parts(value: Any) -> tuple[str, ...]:
    normalized = _IDENTIFIER_QUOTE_RE.sub("", str(value or "")).strip().lower()
    return tuple(part for part in normalized.split(".") if part)


def _identifier_matches(expected: Any, actual: Any) -> bool:
    expected_parts = _identifier_parts(expected)
    actual_parts = _identifier_parts(actual)
    if not expected_parts or not actual_parts:
        return False
    shorter = min(len(expected_parts), len(actual_parts))
    return expected_parts[-shorter:] == actual_parts[-shorter:]


def _dedupe(values: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _canonical_predicate_expression(
    value: Any,
    dialect: Optional[str],
) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        expression = parse_one(text, read=str(dialect or "").strip() or None)
    except Exception:
        return _IDENTIFIER_QUOTE_RE.sub("", text).strip().lower()
    normalized = expression.copy().transform(
        lambda node: exp.column(str(node.name or ""))
        if isinstance(node, exp.Column)
        else node
    )
    return normalized.sql(dialect=str(dialect or "").strip() or None).casefold()


def build_schema_evidence(
    existing_metadata: Dict[str, Any],
    result_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge one schema retrieval into turn-local cumulative evidence."""
    previous = existing_metadata.get("schema_evidence")
    previous = dict(previous) if isinstance(previous, dict) else {}

    tables = _dedupe(
        list(previous.get("tables") or [])
        + list(result_metadata.get("selected_tables") or [])
    )
    columns = _dedupe(
        list(previous.get("columns") or [])
        + list(result_metadata.get("selected_column_refs") or [])
    )

    table_columns: Dict[str, List[str]] = {}
    raw_previous_table_columns = previous.get("table_columns")
    if isinstance(raw_previous_table_columns, dict):
        for table, field_names in raw_previous_table_columns.items():
            table_columns[str(table)] = _dedupe(list(field_names or []))

    raw_selected_columns = result_metadata.get("selected_columns")
    if isinstance(raw_selected_columns, dict):
        for table, field_names in raw_selected_columns.items():
            table_name = str(table)
            table_columns[table_name] = _dedupe(
                table_columns.get(table_name, []) + list(field_names or [])
            )

    table_primary_keys: Dict[str, List[str]] = {}
    raw_previous_primary_keys = previous.get("table_primary_keys")
    if isinstance(raw_previous_primary_keys, dict):
        for table, field_names in raw_previous_primary_keys.items():
            table_primary_keys[str(table)] = _dedupe(list(field_names or []))
    raw_selected_primary_keys = result_metadata.get("selected_primary_keys")
    if isinstance(raw_selected_primary_keys, dict):
        for table, field_names in raw_selected_primary_keys.items():
            table_name = str(table)
            table_primary_keys[table_name] = _dedupe(
                table_primary_keys.get(table_name, []) + list(field_names or [])
            )

    return {
        "tables": tables,
        "columns": columns,
        "table_columns": table_columns,
        "table_primary_keys": table_primary_keys,
        "retrieval_count": int(previous.get("retrieval_count") or 0) + 1,
    }


def validate_query_plan_evidence(
    plan: QueryPlan,
    context_metadata: Dict[str, Any],
) -> QueryPlanCheck:
    """Require every planned physical table and column to come from retrieval."""
    evidence = context_metadata.get("schema_evidence")
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    evidence_tables = list(evidence.get("tables") or [])
    evidence_columns = list(evidence.get("columns") or [])

    issues: List[str] = []
    if plan.unresolved_questions:
        issues.append("unresolved_questions")
    if not evidence_tables:
        issues.append("schema_evidence_missing")

    for table in plan.source_tables:
        if not any(_identifier_matches(table, item) for item in evidence_tables):
            issues.append(f"table_not_in_schema_evidence:{table}")

    for column in plan.required_columns:
        if len(_identifier_parts(column)) < 2:
            issues.append(f"column_not_qualified:{column}")
            continue
        if not any(_identifier_matches(column, item) for item in evidence_columns):
            issues.append(f"column_not_in_schema_evidence:{column}")

    return QueryPlanCheck(
        issues=_dedupe(issues),
        evidence={
            "evidence_table_count": len(evidence_tables),
            "evidence_column_count": len(evidence_columns),
            "retrieval_count": int(evidence.get("retrieval_count") or 0),
        },
    )


def validate_query_plan_intent(
    plan: QueryPlan,
    context_metadata: Dict[str, Any],
    raw_user_message: Optional[str] = None,
) -> QueryPlanCheck:
    """Reject a narrow class of unsupported aggregation semantics."""
    issues: List[str] = []
    request_text = str(raw_user_message or "")
    evidence = context_metadata.get("schema_evidence")
    evidence = dict(evidence) if isinstance(evidence, dict) else {}
    primary_keys = evidence.get("table_primary_keys")
    primary_keys = dict(primary_keys) if isinstance(primary_keys, dict) else {}

    if (
        len(plan.source_tables) == 1
        and not plan.join_path
        and not _EXPLICIT_DISTINCT_REQUEST_RE.search(request_text)
    ):
        source_table = plan.source_tables[0]
        source_primary_keys: List[str] = []
        for table_name, field_names in primary_keys.items():
            if _identifier_matches(source_table, table_name):
                source_primary_keys.extend(str(item) for item in field_names or [])

        for expression in plan.metric_expressions:
            match = _COUNT_DISTINCT_RE.search(str(expression))
            if not match:
                continue
            counted_column = match.group(1)
            if any(
                _identifier_matches(counted_column, primary_key)
                for primary_key in source_primary_keys
            ):
                issues.append(
                    f"unrequested_distinct_primary_key_count:{counted_column}"
                )

    return QueryPlanCheck(
        issues=_dedupe(issues),
        evidence={"intent_request_available": bool(request_text.strip())},
    )


def _parse_sql_details(sql: str, dialect: Optional[str]) -> Dict[str, Any]:
    read_dialect = str(dialect or "").strip() or None
    try:
        statement = parse_one(sql, read=read_dialect)
    except Exception:
        return {
            "tables": [],
            "columns": [],
            "where_columns": [],
            "filter_expressions": [],
        }

    cte_aliases = {
        str(cte.alias_or_name or "").strip().lower()
        for cte in statement.find_all(exp.CTE)
        if str(cte.alias_or_name or "").strip()
    }
    tables: List[str] = []
    for table in statement.find_all(exp.Table):
        table_name = str(table.name or "").strip()
        if not table_name or table_name.lower() in cte_aliases:
            continue
        parts = [
            str(getattr(table, part, "") or "").strip()
            for part in ("catalog", "db", "name")
        ]
        tables.append(".".join(part for part in parts if part))

    columns = [
        str(column.name or "").strip()
        for column in statement.find_all(exp.Column)
        if str(column.name or "").strip()
    ]
    where_columns: List[str] = []
    filter_expressions: List[str] = []
    for predicate in [
        *statement.find_all(exp.Where),
        *statement.find_all(exp.Having),
    ]:
        where_columns.extend(
            str(column.name or "").strip()
            for column in predicate.find_all(exp.Column)
            if str(column.name or "").strip()
        )
        filter_expressions.extend(
            _canonical_predicate_expression(node, dialect)
            for node in predicate.this.walk()
            if isinstance(node, (exp.Column, exp.AggFunc))
        )

    return {
        "tables": _dedupe(tables),
        "columns": _dedupe(columns),
        "where_columns": _dedupe(where_columns),
        "filter_expressions": _dedupe(filter_expressions),
    }


def assess_query_plan_risk(
    sql: str,
    context_metadata: Dict[str, Any],
    *,
    dialect: Optional[str] = None,
) -> QueryPlanRiskAssessment:
    """Require plans for structurally risky SQL without dataset-specific rules.

    The fast path is intentionally narrow: one evidence-backed physical table,
    one SELECT statement, and no join, subquery, window, distinct, HAVING,
    set operation, rollup, grouping sets, or detected time-series shape.
    Simple filters and one-table grouped aggregations remain eligible.
    """
    shape = analyze_sql_shape(sql, dialect=dialect)
    details = _parse_sql_details(sql, dialect)
    actual_tables = details["tables"] or list(shape.table_references)
    raw_evidence = context_metadata.get("schema_evidence")
    schema_evidence = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    evidence_tables = list(schema_evidence.get("tables") or [])
    reasons: List[str] = []

    if shape.statement_count != 1 or not shape.has_select or not shape.has_from:
        reasons.append("unsupported_statement_shape")
    if not evidence_tables:
        reasons.append("schema_evidence_missing")
    for table in actual_tables:
        if not any(_identifier_matches(table, item) for item in evidence_tables):
            reasons.append(f"table_not_in_schema_evidence:{table}")
    if len(actual_tables) != 1:
        reasons.append(f"physical_table_count:{len(actual_tables)}")
    if shape.has_join:
        reasons.append("join")
    if shape.has_outer_join:
        reasons.append("outer_join")
    if shape.has_cross_join:
        reasons.append("cross_join")
    if shape.has_subquery or shape.cte_count:
        reasons.append("subquery_or_cte")
    if shape.has_window_function or shape.has_over:
        reasons.append("window")
    if shape.has_distinct or re.search(
        r"(?i)\bcount\s*\(\s*distinct\b",
        sql,
    ):
        reasons.append("distinct")
    if shape.has_having:
        reasons.append("having")
    if shape.has_set_operation:
        reasons.append("set_operation")
    if shape.has_rollup or shape.has_grouping_sets or shape.has_grouping:
        reasons.append("advanced_grouping")
    if shape.has_time_series:
        reasons.append("time_series")

    deduped_reasons = _dedupe(reasons)
    requires_plan = bool(deduped_reasons)
    return QueryPlanRiskAssessment(
        requires_plan=requires_plan,
        risk_level="high" if requires_plan else "low",
        reasons=deduped_reasons,
        evidence={
            "physical_table_count": len(actual_tables),
            "evidence_table_count": len(evidence_tables),
            "feature_names": list(shape.feature_names),
        },
    )


def validate_sql_against_query_plan(
    plan: QueryPlan,
    sql: str,
    *,
    dialect: Optional[str] = None,
) -> QueryPlanCheck:
    """Reject SQL that drifts from the accepted runtime plan."""
    shape = analyze_sql_shape(sql, dialect=dialect)
    details = _parse_sql_details(sql, dialect)
    actual_tables = details["tables"] or list(shape.table_references)
    actual_columns = details["columns"]
    where_columns = details["where_columns"]
    filter_expressions = set(details["filter_expressions"])
    issues: List[str] = []

    for table in plan.source_tables:
        if not any(_identifier_matches(table, item) for item in actual_tables):
            issues.append(f"planned_table_missing_from_sql:{table}")
    for table in actual_tables:
        if not any(_identifier_matches(table, item) for item in plan.source_tables):
            issues.append(f"unplanned_table_in_sql:{table}")

    actual_column_names = {
        parts[-1] for value in actual_columns if (parts := _identifier_parts(value))
    }
    for column in plan.required_columns:
        column_parts = _identifier_parts(column)
        if column_parts and column_parts[-1] not in actual_column_names:
            issues.append(f"planned_column_missing_from_sql:{column}")

    where_column_names = {
        parts[-1] for value in where_columns if (parts := _identifier_parts(value))
    }
    for query_filter in plan.filters:
        planned_expression = _canonical_predicate_expression(
            query_filter.column,
            dialect,
        )
        if planned_expression and planned_expression in filter_expressions:
            continue
        column_parts = _identifier_parts(query_filter.column)
        if column_parts and column_parts[-1] not in where_column_names:
            issues.append(f"planned_filter_missing_from_sql:{query_filter.column}")

    projection_count = len(shape.select_items)
    if projection_count != len(plan.output_columns):
        issues.append(
            "output_column_count_mismatch:"
            f"{projection_count}!={len(plan.output_columns)}"
        )
    if plan.requires_aggregation and not shape.has_aggregation:
        issues.append("planned_aggregation_missing_from_sql")
    if plan.requires_grouping and not shape.has_group_by:
        issues.append("planned_grouping_missing_from_sql")
    if plan.requires_ordering and not shape.has_order_by:
        issues.append("planned_ordering_missing_from_sql")
    if plan.limit is not None and not shape.has_limit:
        issues.append("planned_limit_missing_from_sql")

    return QueryPlanCheck(
        issues=_dedupe(issues),
        evidence={
            "planned_tables": list(plan.source_tables),
            "sql_tables": actual_tables,
            "planned_output_count": len(plan.output_columns),
            "sql_projection_count": projection_count,
        },
    )
