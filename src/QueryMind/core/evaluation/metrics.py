"""Deterministic metrics for QueryMind Text2SQL evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from sqlglot import exp, parse_one

from .base import (
    AgentResult,
    EvaluationResult,
    ExpectedSqlContract,
    SqlExecutionArtifact,
    SqlTestCase,
)


def _normalize_cell(
    value: Any,
    *,
    numeric_decimal_places: Optional[int] = None,
    value_aliases: Optional[Dict[str, str]] = None,
) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if numeric_decimal_places is not None:
            quantum = Decimal(1).scaleb(-numeric_decimal_places)
            value = value.quantize(quantum, rounding=ROUND_HALF_UP)
            return format(value, f".{numeric_decimal_places}f")
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        if numeric_decimal_places is not None:
            quantum = Decimal(1).scaleb(-numeric_decimal_places)
            normalized = Decimal(str(value)).quantize(
                quantum,
                rounding=ROUND_HALF_UP,
            )
            return format(normalized, f".{numeric_decimal_places}f")
        return format(value, ".15g")
    if isinstance(value, str):
        alias = (value_aliases or {}).get(value.strip().casefold())
        return alias if alias is not None else value
    if isinstance(value, (int, bool)):
        return value
    return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint_dataframe(
    df: pd.DataFrame,
    *,
    numeric_decimal_places: Optional[int] = None,
    value_aliases: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    """Build full-result hashes without persisting business result rows."""
    normalized_aliases: Dict[str, str] = {}
    for canonical, aliases in (value_aliases or {}).items():
        canonical_value = str(canonical).strip()
        if not canonical_value:
            continue
        normalized_aliases[canonical_value.casefold()] = canonical_value
        for alias in aliases:
            alias_value = str(alias).strip()
            if alias_value:
                normalized_aliases[alias_value.casefold()] = canonical_value
    rows = [
        [
            _normalize_cell(
                value,
                numeric_decimal_places=numeric_decimal_places,
                value_aliases=normalized_aliases,
            )
            for value in row
        ]
        for row in df.itertuples(index=False, name=None)
    ]
    serialized_rows = [_stable_json(row) for row in rows]
    columns = [str(column).strip().lower() for column in df.columns]
    return {
        "ordered_result_fingerprint": _sha256(_stable_json(serialized_rows)),
        "unordered_result_fingerprint": _sha256(_stable_json(sorted(serialized_rows))),
        "column_fingerprint": _sha256(_stable_json(columns)),
    }


def sql_requires_order(sql: str, dialect: Optional[str] = None) -> bool:
    try:
        parsed = parse_one(sql, read=dialect or None)
    except Exception:
        return bool("order by" in sql.lower())
    return parsed.find(exp.Order) is not None


def compare_execution_artifacts(
    agent: Optional[SqlExecutionArtifact],
    ground_truth: Optional[SqlExecutionArtifact],
    *,
    order_sensitive: bool,
    use_comparison_fingerprints: bool = False,
    compare_column_names: bool = False,
) -> Dict[str, Any]:
    if not agent or not ground_truth or not agent.success or not ground_truth.success:
        return {
            "result_correct": False,
            "row_count_match": False,
            "column_count_match": False,
            "column_names_match": False,
            "order_sensitive": order_sensitive,
        }

    row_count_match = agent.row_count == ground_truth.row_count
    column_count_match = len(agent.column_names) == len(ground_truth.column_names)
    column_names_match = [str(value).lower() for value in agent.column_names] == [
        str(value).lower() for value in ground_truth.column_names
    ]
    if use_comparison_fingerprints:
        agent_fingerprint = (
            agent.comparison_ordered_result_fingerprint
            if order_sensitive
            else agent.comparison_unordered_result_fingerprint
        )
        ground_truth_fingerprint = (
            ground_truth.comparison_ordered_result_fingerprint
            if order_sensitive
            else ground_truth.comparison_unordered_result_fingerprint
        )
    else:
        agent_fingerprint = (
            agent.ordered_result_fingerprint
            if order_sensitive
            else agent.unordered_result_fingerprint
        )
        ground_truth_fingerprint = (
            ground_truth.ordered_result_fingerprint
            if order_sensitive
            else ground_truth.unordered_result_fingerprint
        )
    values_match = bool(
        agent_fingerprint
        and ground_truth_fingerprint
        and agent_fingerprint == ground_truth_fingerprint
    )
    if agent.row_count == 0 and ground_truth.row_count == 0:
        # Empty row hashes contain no semantic evidence, so compare the output
        # labels conservatively instead of treating any same-width query as equal.
        values_match = column_names_match
    if compare_column_names and not column_names_match:
        values_match = False
    return {
        "result_correct": bool(row_count_match and column_count_match and values_match),
        "row_count_match": row_count_match,
        "column_count_match": column_count_match,
        "column_names_match": column_names_match,
        "values_match": values_match,
        "order_sensitive": order_sensitive,
    }


def normalize_table_name(value: str) -> str:
    parts = [part.strip(' "`[]').lower() for part in str(value).split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return parts[0] if parts else ""


def _normalize_identifier(value: Any) -> str:
    parts = [part.strip(' "`[]').lower() for part in str(value).split(".") if part]
    return parts[-1] if parts else ""


def evaluate_sql_contract(
    sql: str,
    contract: Optional[ExpectedSqlContract],
    *,
    dialect: Optional[str] = None,
) -> Dict[str, Any]:
    """Check dataset-declared SQL requirements without executing business data."""
    if contract is None:
        return {
            "sql_contract_passed": True,
            "sql_contract_violations": [],
            "sql_contract_evidence": {},
        }

    try:
        parsed = parse_one(sql, read=dialect or None)
    except Exception:
        return {
            "sql_contract_passed": False,
            "sql_contract_violations": ["sql_parse_error"],
            "sql_contract_evidence": {},
        }

    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    columns = {
        _normalize_identifier(column.name)
        for column in parsed.find_all(exp.Column)
        if _normalize_identifier(column.name)
    }
    filter_columns: set[str] = set()
    for clause_type in (exp.Where, exp.Having):
        for clause in parsed.find_all(clause_type):
            filter_columns.update(
                _normalize_identifier(column.name)
                for column in clause.find_all(exp.Column)
                if _normalize_identifier(column.name)
            )

    projections = list(select.expressions) if isinstance(select, exp.Select) else []
    projection_aliases = {
        _normalize_identifier(expression.alias_or_name)
        for expression in projections
        if _normalize_identifier(expression.alias_or_name)
    }
    feature_presence = {
        "aggregation": any(True for _ in parsed.find_all(exp.AggFunc)),
        "case_when": any(True for _ in parsed.find_all(exp.Case)),
        "cte": any(True for _ in parsed.find_all(exp.With)),
        "distinct": any(True for _ in parsed.find_all(exp.Distinct)),
        "group_by": any(True for _ in parsed.find_all(exp.Group)),
        "having": any(True for _ in parsed.find_all(exp.Having)),
        "join": any(True for _ in parsed.find_all(exp.Join)),
        "limit": any(True for _ in parsed.find_all(exp.Limit)),
        "order_by": any(True for _ in parsed.find_all(exp.Order)),
        "offset": any(True for _ in parsed.find_all(exp.Offset)),
        "subquery": (
            any(True for _ in parsed.find_all(exp.Subquery))
            or any(True for _ in parsed.find_all(exp.Exists))
            or sum(1 for _ in parsed.find_all(exp.Select)) > 1
        ),
        "where": any(True for _ in parsed.find_all(exp.Where)),
        "window": any(True for _ in parsed.find_all(exp.Window)),
    }

    required_features = {
        _normalize_identifier(value) for value in contract.required_features
    }
    forbidden_features = {
        _normalize_identifier(value) for value in contract.forbidden_features
    }
    required_columns = {
        _normalize_identifier(value) for value in contract.required_columns
    }
    forbidden_columns = {
        _normalize_identifier(value) for value in contract.forbidden_columns
    }
    required_filter_columns = {
        _normalize_identifier(value) for value in contract.required_filter_columns
    }
    required_projection_aliases = {
        _normalize_identifier(value)
        for value in contract.required_projection_aliases
    }

    violations: List[str] = []
    for feature in sorted(required_features):
        if not feature_presence.get(feature, False):
            violations.append(f"missing_feature:{feature}")
    for feature in sorted(forbidden_features):
        if feature_presence.get(feature, False):
            violations.append(f"forbidden_feature:{feature}")
    for column in sorted(required_columns - columns):
        violations.append(f"missing_column:{column}")
    for column in sorted(forbidden_columns & columns):
        violations.append(f"forbidden_column:{column}")
    for column in sorted(required_filter_columns - filter_columns):
        violations.append(f"missing_filter_column:{column}")
    for alias in sorted(required_projection_aliases - projection_aliases):
        violations.append(f"missing_projection_alias:{alias}")

    projection_count = len(projections)
    if (
        contract.min_projection_count is not None
        and projection_count < contract.min_projection_count
    ):
        violations.append(
            f"projection_count_below_min:{projection_count}<{contract.min_projection_count}"
        )
    if (
        contract.max_projection_count is not None
        and projection_count > contract.max_projection_count
    ):
        violations.append(
            f"projection_count_above_max:{projection_count}>{contract.max_projection_count}"
        )

    return {
        "sql_contract_passed": not violations,
        "sql_contract_violations": violations,
        "sql_contract_evidence": {
            "features": sorted(
                name for name, present in feature_presence.items() if present
            ),
            "columns": sorted(columns),
            "filter_columns": sorted(filter_columns),
            "projection_aliases": sorted(projection_aliases),
            "projection_count": projection_count,
        },
    }


def calculate_schema_recall(
    test_case: SqlTestCase,
    agent_result: AgentResult,
) -> Dict[str, Any]:
    expected = {
        normalize_table_name(table)
        for table in (test_case.expected_schema.tables if test_case.expected_schema else [])
        if normalize_table_name(table)
    }
    if not expected:
        return {
            "schema_recall": None,
            "expected_tables": [],
            "retrieved_tables": [],
            "missing_tables": [],
        }

    retrieved: set[str] = set()
    for record in agent_result.tool_calls:
        if record.tool_name == "run_sql":
            break
        if record.tool_name != "schema_retrieve":
            continue
        for table in record.metadata.get("selected_tables", []) or []:
            normalized = normalize_table_name(str(table))
            if normalized:
                retrieved.add(normalized)

    matched = expected & retrieved
    return {
        "schema_recall": len(matched) / len(expected),
        "expected_tables": sorted(expected),
        "retrieved_tables": sorted(retrieved),
        "missing_tables": sorted(expected - retrieved),
    }


def enrich_result_metrics(result: EvaluationResult) -> None:
    """Attach stable per-case metrics after all evaluators finish."""
    schema_metrics = calculate_schema_recall(result.test_case, result.agent_result)
    run_sql_calls = result.agent_result.get_tool_calls("run_sql")
    schema_calls = result.agent_result.get_tool_calls("schema_retrieve")
    result.metadata.update(schema_metrics)
    result.metadata.update(
        {
            "tool_call_count": len(result.agent_result.tool_calls),
            "schema_retrieve_calls": len(schema_calls),
            "run_sql_calls": len(run_sql_calls),
        }
    )
    result.metadata["first_sql_execution_success"] = (
        bool(run_sql_calls[0].success) if run_sql_calls else False
    )


def percentile(values: Iterable[float], fraction: float) -> float:
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    if len(items) == 1:
        return items[0]
    position = max(0.0, min(1.0, fraction)) * (len(items) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return items[lower]
    weight = position - lower
    return items[lower] * (1 - weight) + items[upper] * weight


def merge_token_usage(usages: Iterable[Dict[str, int] | None]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            if isinstance(value, bool):
                continue
            try:
                merged[key] = merged.get(key, 0) + int(value)
            except (TypeError, ValueError):
                continue
    return merged


def estimate_usage_cost_usd(
    usage: Dict[str, int],
    pricing: Dict[str, Any] | None,
) -> Optional[float]:
    if not pricing:
        return None
    try:
        hit_price = float(pricing.get("input_cache_hit_usd_per_million"))
        miss_price = float(pricing.get("input_cache_miss_usd_per_million"))
        output_price = float(pricing.get("output_usd_per_million"))
    except (TypeError, ValueError):
        return None

    hit_tokens = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    prompt_tokens = int(
        usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    )
    miss_tokens = int(
        usage.get("prompt_cache_miss_tokens", max(0, prompt_tokens - hit_tokens))
        or 0
    )
    output_tokens = int(
        usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    )
    return (
        hit_tokens * hit_price
        + miss_tokens * miss_price
        + output_tokens * output_price
    ) / 1_000_000
