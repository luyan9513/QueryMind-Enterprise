"""Data-source-owned semantic contracts for deterministic Text2SQL checks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlglot import exp, parse_one

from .._compat import StrEnum


class SemanticContractMode(StrEnum):
    DISABLED = "disabled"
    ADVISORY = "advisory"
    REQUIRED = "required"


def parse_semantic_contract_mode(
    value: str | SemanticContractMode | None,
) -> SemanticContractMode:
    if isinstance(value, SemanticContractMode):
        return value
    normalized = str(value or "disabled").strip().lower().replace("-", "_")
    aliases = {
        "off": SemanticContractMode.DISABLED,
        "false": SemanticContractMode.DISABLED,
        "disabled": SemanticContractMode.DISABLED,
        "observe": SemanticContractMode.ADVISORY,
        "advisory": SemanticContractMode.ADVISORY,
        "on": SemanticContractMode.REQUIRED,
        "true": SemanticContractMode.REQUIRED,
        "required": SemanticContractMode.REQUIRED,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            "Semantic contract mode must be one of: disabled, advisory, required"
        ) from exc


class SemanticMetricContract(BaseModel):
    """One approved business metric owned by a data source."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    status: str = Field(default="approved", pattern="^(draft|approved|deprecated)$")
    owner: str = Field(min_length=1)
    source_tables: list[str] = Field(min_length=1)
    required_columns: list[str] = Field(default_factory=list)
    accepted_expressions: list[str] = Field(min_length=1)
    output_alias: str | None = None
    accepted_output_aliases: list[str] = Field(default_factory=list)
    base_grain: str = Field(min_length=1)
    default_time_column: str | None = None
    required_filter_columns: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def search_terms(self) -> list[str]:
        return _dedupe([self.name, *self.aliases])

    def as_runtime_metadata(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SemanticContractCatalog(BaseModel):
    """Versioned semantic definitions for exactly one data source."""

    data_source_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    metrics: list[SemanticMetricContract] = Field(default_factory=list)

    def approved_metrics(self) -> list[SemanticMetricContract]:
        return [metric for metric in self.metrics if metric.status == "approved"]

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def match(self, query: str) -> list[SemanticMetricContract]:
        text = str(query or "").casefold()
        candidates: list[tuple[SemanticMetricContract, str]] = []
        for metric in self.approved_metrics():
            terms = [
                term for term in metric.search_terms() if _term_matches(text, term)
            ]
            if terms:
                candidates.append(
                    (metric, max(terms, key=lambda item: len(item.casefold())))
                )
        candidates.sort(key=lambda item: len(item[1].casefold()), reverse=True)
        selected: list[tuple[SemanticMetricContract, str]] = []
        for metric, term in candidates:
            normalized = term.casefold()
            if any(
                normalized != chosen.casefold()
                and normalized in chosen.casefold()
                for _, chosen in selected
            ):
                continue
            selected.append((metric, term))
        return [metric for metric, _ in selected]

    def build_runtime_snapshot(self, query: str) -> dict[str, Any]:
        matched = self.match(query)
        return {
            "data_source_id": self.data_source_id,
            "version": self.version,
            "matched_metric_ids": [metric.id for metric in matched],
            "metrics": [metric.as_runtime_metadata() for metric in matched],
        }


@dataclass(slots=True)
class SemanticContractCheck:
    issues: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.issues


def load_semantic_contract_catalog(path: str | Path) -> SemanticContractCatalog:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Semantic contract catalog not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    catalog = SemanticContractCatalog.model_validate(payload)
    metric_ids = [metric.id for metric in catalog.metrics]
    duplicates = sorted({item for item in metric_ids if metric_ids.count(item) > 1})
    if duplicates:
        raise ValueError(
            "Semantic contract IDs must be unique: " + ", ".join(duplicates)
        )
    return catalog


def format_semantic_contracts_for_llm(snapshot: dict[str, Any]) -> str:
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        return ""
    lines = [
        "## Approved data-source semantic contracts",
        f"Catalog version: {snapshot.get('version')}",
        "Copy the applicable contract ID and catalog version into the query plan.",
    ]
    for raw in metrics:
        if not isinstance(raw, dict):
            continue
        lines.extend(
            [
                f"- Contract `{raw.get('id')}`: {raw.get('name')}",
                f"  Definition: {raw.get('description')}",
                "  Accepted metric SQL: "
                + " OR ".join(str(item) for item in raw.get("accepted_expressions") or []),
                "  Required tables: "
                + ", ".join(str(item) for item in raw.get("source_tables") or []),
                "  Required columns: "
                + ", ".join(str(item) for item in raw.get("required_columns") or []),
                f"  Base grain: {raw.get('base_grain')}",
            ]
        )
        output_aliases = _contract_output_aliases(raw)
        if output_aliases:
            lines.append("  Accepted metric aliases: " + ", ".join(output_aliases))
        if raw.get("default_time_column"):
            lines.append(f"  Default time column: {raw['default_time_column']}")
        if raw.get("required_filter_columns"):
            lines.append(
                "  Required filter columns: "
                + ", ".join(
                    str(item) for item in raw.get("required_filter_columns") or []
                )
            )
        if raw.get("notes"):
            lines.append(
                "  Data-source notes: "
                + " ".join(str(item) for item in raw.get("notes") or [])
            )
    return "\n".join(lines)


def validate_query_plan_semantic_contracts(
    plan: Any,
    context_metadata: dict[str, Any],
    *,
    mode: str | SemanticContractMode,
    dialect: str | None = None,
) -> SemanticContractCheck:
    resolved = parse_semantic_contract_mode(mode)
    if resolved == SemanticContractMode.DISABLED:
        return SemanticContractCheck(evidence={"mode": resolved.value})

    snapshot = _runtime_snapshot(context_metadata)
    metrics = _metric_map(snapshot)
    cited_ids = _dedupe(list(getattr(plan, "semantic_contract_ids", []) or []))
    metric_intent = bool(
        getattr(plan, "requires_aggregation", False)
        or list(getattr(plan, "metric_expressions", []) or [])
    )
    issues: list[str] = []

    if (
        metric_intent
        and metrics
        and resolved == SemanticContractMode.REQUIRED
        and not cited_ids
    ):
        issues.append("semantic_contract_not_cited")
    version = str(getattr(plan, "semantic_contract_version", None) or "")
    if cited_ids and version != str(snapshot.get("version") or ""):
        issues.append("semantic_contract_version_mismatch")

    plan_tables = list(getattr(plan, "source_tables", []) or [])
    plan_columns = list(getattr(plan, "required_columns", []) or [])
    plan_expressions = list(getattr(plan, "metric_expressions", []) or [])
    plan_outputs = list(getattr(plan, "output_columns", []) or [])
    advisories: list[str] = []
    for contract_id in cited_ids:
        contract = metrics.get(contract_id)
        if contract is None:
            issues.append(f"semantic_contract_not_retrieved:{contract_id}")
            continue
        for table in contract.get("source_tables") or []:
            if not any(_identifier_matches(table, item) for item in plan_tables):
                issues.append(f"contract_table_missing_from_plan:{contract_id}:{table}")
        for column in contract.get("required_columns") or []:
            if not any(_identifier_matches(column, item) for item in plan_columns):
                issues.append(f"contract_column_missing_from_plan:{contract_id}:{column}")
        accepted_expressions = list(contract.get("accepted_expressions") or [])
        if accepted_expressions and not _expressions_compatible(
            accepted_expressions,
            plan_expressions,
            dialect,
        ):
            issues.append(f"contract_expression_missing_from_plan:{contract_id}")
        output_aliases = _contract_output_aliases(contract)
        if output_aliases and not any(
            _identifier_matches(alias, item)
            for alias in output_aliases
            for item in plan_outputs
        ):
            advisories.append(
                f"contract_output_alias_differs_from_preference:{contract_id}"
            )

    return SemanticContractCheck(
        issues=_dedupe(issues),
        advisories=_dedupe(advisories),
        evidence={
            "mode": resolved.value,
            "catalog_version": snapshot.get("version"),
            "matched_metric_ids": list(snapshot.get("matched_metric_ids") or []),
            "cited_metric_ids": cited_ids,
        },
    )


def validate_sql_against_semantic_contracts(
    plan: Any | None,
    sql: str,
    context_metadata: dict[str, Any],
    *,
    mode: str | SemanticContractMode,
    dialect: str | None = None,
) -> SemanticContractCheck:
    resolved = parse_semantic_contract_mode(mode)
    if resolved == SemanticContractMode.DISABLED:
        return SemanticContractCheck(evidence={"mode": resolved.value})

    try:
        statement = parse_one(sql, read=str(dialect or "").strip() or None)
    except Exception:
        return SemanticContractCheck(
            issues=["semantic_contract_sql_parse_error"],
            evidence={"mode": resolved.value},
        )

    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    has_aggregation = any(True for _ in statement.find_all(exp.AggFunc))
    snapshot = _runtime_snapshot(context_metadata)
    metrics = _metric_map(snapshot)
    if plan is None:
        issues = (
            ["semantic_contract_plan_required"]
            if has_aggregation
            and metrics
            and resolved == SemanticContractMode.REQUIRED
            else []
        )
        return SemanticContractCheck(
            issues=issues,
            evidence={"mode": resolved.value, "has_aggregation": has_aggregation},
        )

    plan_check = validate_query_plan_semantic_contracts(
        plan,
        context_metadata,
        mode=resolved,
        dialect=dialect,
    )
    issues = list(plan_check.issues)
    cited_ids = list(getattr(plan, "semantic_contract_ids", []) or [])
    tables = [_qualified_table_name(item) for item in statement.find_all(exp.Table)]
    columns = [str(item.name or "") for item in statement.find_all(exp.Column)]
    where_columns = [
        str(column.name or "")
        for where in statement.find_all(exp.Where)
        for column in where.find_all(exp.Column)
    ]
    projection_expressions: set[str] = set()
    projection_expression_sql: list[str] = []
    projection_aliases: list[str] = []
    advisories = list(plan_check.advisories)
    selects = list(statement.find_all(exp.Select))
    if isinstance(statement, exp.Select) and statement not in selects:
        selects.insert(0, statement)
    for candidate_select in selects:
        for projection in candidate_select.expressions:
            projection_aliases.append(str(projection.alias_or_name or ""))
            base = projection.this if isinstance(projection, exp.Alias) else projection
            projection_expressions.update(
                _canonical_expression_node(node, dialect)
                for node in base.walk()
                if isinstance(node, exp.Expression)
            )
            projection_expression_sql.extend(
                node.sql(dialect=str(dialect or "").strip() or None)
                for node in base.walk()
                if isinstance(node, exp.Expression)
            )

    for contract_id in cited_ids:
        contract = metrics.get(contract_id)
        if contract is None:
            continue
        for table in contract.get("source_tables") or []:
            if not any(_identifier_matches(table, item) for item in tables):
                issues.append(f"contract_table_missing_from_sql:{contract_id}:{table}")
        for column in contract.get("required_columns") or []:
            if not any(_identifier_matches(column, item) for item in columns):
                issues.append(f"contract_column_missing_from_sql:{contract_id}:{column}")
        accepted_expressions = list(contract.get("accepted_expressions") or [])
        accepted = {
            _canonical_expression(item, dialect) for item in accepted_expressions
        }
        if (
            accepted
            and accepted.isdisjoint(projection_expressions)
            and not _expressions_compatible(
                accepted_expressions,
                projection_expression_sql,
                dialect,
            )
        ):
            issues.append(f"contract_expression_missing_from_sql:{contract_id}")
        output_aliases = _contract_output_aliases(contract)
        if output_aliases and not any(
            _identifier_matches(alias, item)
            for alias in output_aliases
            for item in projection_aliases
        ):
            advisories.append(
                f"contract_output_alias_differs_from_preference:{contract_id}"
            )
        for column in contract.get("required_filter_columns") or []:
            if not any(_identifier_matches(column, item) for item in where_columns):
                issues.append(f"contract_filter_missing_from_sql:{contract_id}:{column}")

    evidence = dict(plan_check.evidence)
    evidence.update(
        {
            "has_aggregation": has_aggregation,
            "sql_projection_count": len(select.expressions) if select is not None else 0,
        }
    )
    return SemanticContractCheck(
        issues=_dedupe(issues),
        advisories=_dedupe(advisories),
        evidence=evidence,
    )


def _runtime_snapshot(context_metadata: dict[str, Any]) -> dict[str, Any]:
    raw = context_metadata.get("semantic_contracts")
    return dict(raw) if isinstance(raw, dict) else {}


def _metric_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in snapshot.get("metrics") or []:
        if isinstance(raw, dict) and raw.get("id"):
            result[str(raw["id"])] = raw
    return result


def _contract_output_aliases(contract: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            contract.get("output_alias"),
            *(contract.get("accepted_output_aliases") or []),
            contract.get("name"),
            *(contract.get("aliases") or []),
        ]
    )


def _term_matches(text: str, term: str) -> bool:
    needle = str(term or "").strip().casefold()
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9_ -]+", needle):
        return re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", text) is not None
    return needle in text


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _identifier_parts(value: Any) -> tuple[str, ...]:
    normalized = re.sub(r"[`\"\[\]]", "", str(value or "")).strip().casefold()
    return tuple(part for part in normalized.split(".") if part)


def _identifier_matches(expected: Any, actual: Any) -> bool:
    left = _identifier_parts(expected)
    right = _identifier_parts(actual)
    if not left or not right:
        return False
    width = min(len(left), len(right))
    return left[-width:] == right[-width:]


def _qualified_table_name(table: exp.Table) -> str:
    parts = [
        str(getattr(table, part, "") or "").strip()
        for part in ("catalog", "db", "name")
    ]
    return ".".join(part for part in parts if part)


def _parse_expression_select(value: Any, dialect: str | None) -> exp.Expression:
    text = str(value or "")
    candidates = [text]
    without_alias = re.sub(r"(?is)\s+as\s+[^()]+$", "", text).strip()
    if without_alias != text:
        candidates.append(without_alias)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return parse_one(
                f"SELECT {candidate}",
                read=str(dialect or "").strip() or None,
            )
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _canonical_expression(value: Any, dialect: str | None) -> str:
    try:
        statement = _parse_expression_select(value, dialect)
        select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
        if select is None or not select.expressions:
            return ""
        node = select.expressions[0]
        if isinstance(node, exp.Alias):
            node = node.this
        return _canonical_expression_node(node, dialect)
    except Exception:
        return " ".join(str(value or "").casefold().split())


def _canonical_expression_node(node: exp.Expression, dialect: str | None) -> str:
    copied = node.copy()
    for column in copied.find_all(exp.Column):
        column.set("catalog", None)
        column.set("db", None)
        column.set("table", None)
    return copied.sql(
        dialect=str(dialect or "").strip() or None,
        normalize=True,
        pretty=False,
    ).casefold()


def _simple_aggregate_signature(
    value: Any,
    dialect: str | None,
) -> tuple[str, str] | None:
    """Return a safe relaxed signature for a one-column aggregate.

    Qualification, DISTINCT, FILTER/CASE conditions, aliases, and outer
    display wrappers do not change the metric identity. Arithmetic inside the
    aggregate remains strict so SUM(price * quantity) cannot match SUM(price).
    """
    try:
        statement = _parse_expression_select(value, dialect)
    except Exception:
        return None
    aggregates = list(statement.find_all(exp.AggFunc))
    if len(aggregates) != 1:
        return None
    aggregate = aggregates[0]
    if any(
        isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod))
        for node in aggregate.walk()
    ):
        return None
    columns = {
        str(column.name or "").strip().casefold()
        for column in aggregate.find_all(exp.Column)
        if str(column.name or "").strip()
    }
    if len(columns) != 1:
        return None
    return aggregate.key.casefold(), next(iter(columns))


def _expressions_compatible(
    accepted_expressions: list[Any],
    candidate_expressions: list[Any],
    dialect: str | None,
) -> bool:
    accepted_canonical = {
        _canonical_expression(item, dialect) for item in accepted_expressions
    }
    candidate_canonical = {
        _canonical_expression(item, dialect) for item in candidate_expressions
    }
    if not accepted_canonical.isdisjoint(candidate_canonical):
        return True
    accepted_signatures = {
        signature
        for item in accepted_expressions
        if (signature := _simple_aggregate_signature(item, dialect)) is not None
    }
    candidate_signatures = {
        signature
        for item in candidate_expressions
        if (signature := _simple_aggregate_signature(item, dialect)) is not None
    }
    if accepted_signatures.intersection(candidate_signatures):
        return True
    return any(
        _candidate_supports_simple_aggregate(item, signature, dialect)
        for item in candidate_expressions
        for signature in accepted_signatures
    )


def _candidate_supports_simple_aggregate(
    value: Any,
    signature: tuple[str, str],
    dialect: str | None,
) -> bool:
    try:
        statement = _parse_expression_select(value, dialect)
    except Exception:
        return False
    aggregates = list(statement.find_all(exp.AggFunc))
    if len(aggregates) != 1:
        return False
    aggregate = aggregates[0]
    function_name, target_column = signature
    if aggregate.key.casefold() != function_name:
        return False
    if any(
        isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod))
        for node in aggregate.walk()
    ):
        return False
    cases = list(aggregate.find_all(exp.Case))
    if not cases:
        return False
    value_columns: set[str] = set()
    for case in cases:
        value_nodes = [case.args.get("default")]
        value_nodes.extend(
            item.args.get("true")
            for item in case.args.get("ifs") or []
            if isinstance(item, exp.If)
        )
        for node in value_nodes:
            if not isinstance(node, exp.Expression):
                continue
            value_columns.update(
                str(column.name or "").strip().casefold()
                for column in node.find_all(exp.Column)
                if str(column.name or "").strip()
            )
    return value_columns == {target_column}


__all__ = [
    "SemanticContractCatalog",
    "SemanticContractCheck",
    "SemanticContractMode",
    "SemanticMetricContract",
    "format_semantic_contracts_for_llm",
    "load_semantic_contract_catalog",
    "parse_semantic_contract_mode",
    "validate_query_plan_semantic_contracts",
    "validate_sql_against_semantic_contracts",
]
