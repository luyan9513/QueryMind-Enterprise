"""Deterministic runtime validation for successful SQL result sets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .query_plan import QueryPlan

_IDENTIFIER_QUOTE_RE = re.compile(r"[`\"\[\]]")
_IDENTIFIER_SEPARATOR_RE = re.compile(r"[\s-]+")
_IDENTIFIER_UNDERSCORE_RE = re.compile(r"_+")


def _column_name(value: Any) -> str:
    normalized = _IDENTIFIER_QUOTE_RE.sub("", str(value or ""))
    normalized = normalized.strip().casefold().split(".")[-1]
    normalized = _IDENTIFIER_SEPARATOR_RE.sub("_", normalized)
    return _IDENTIFIER_UNDERSCORE_RE.sub("_", normalized).strip("_()（）")


def _column_name_candidates(value: Any) -> set[str]:
    raw = _IDENTIFIER_QUOTE_RE.sub("", str(value or "")).strip()
    candidates = {_column_name(raw)}
    opening_positions = [
        position for marker in ("(", "（") if (position := raw.find(marker)) >= 0
    ]
    if opening_positions:
        opening = min(opening_positions)
        candidates.add(_column_name(raw[:opening]))
        if raw.endswith((")", "）")):
            candidates.add(_column_name(raw[opening + 1 : -1]))
    return {candidate for candidate in candidates if candidate}


@dataclass(slots=True)
class ResultValidationCheck:
    """Machine-readable validation outcome without exposing result values."""

    status: str
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "evidence": dict(self.evidence),
        }


def validate_query_result(
    context_metadata: dict[str, Any],
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> ResultValidationCheck:
    """Validate observable result properties against the accepted plan."""

    raw_plan = context_metadata.get("query_plan")
    plan_status = str(context_metadata.get("query_plan_status") or "")
    if not isinstance(raw_plan, dict) or plan_status != "accepted":
        return ResultValidationCheck(
            status="inconclusive",
            issues=["accepted_query_plan_missing"],
            evidence={"row_count": len(rows), "column_count": len(columns)},
        )

    try:
        plan = QueryPlan.model_validate(raw_plan)
    except Exception:
        return ResultValidationCheck(
            status="failed",
            issues=["accepted_query_plan_invalid"],
            evidence={"row_count": len(rows), "column_count": len(columns)},
        )

    issues: list[str] = []
    actual_columns = [_column_name(column) for column in columns]
    planned_columns = [_column_name(column) for column in plan.output_columns]
    actual_column_candidates = [
        _column_name_candidates(column) for column in columns
    ]
    planned_column_candidates = [
        _column_name_candidates(column) for column in plan.output_columns
    ]

    if len(actual_columns) != len(planned_columns):
        issues.append(
            "output_column_count_mismatch:"
            f"{len(planned_columns)}!={len(actual_columns)}"
        )
    else:
        for index, (expected, actual, expected_candidates, actual_candidates) in enumerate(
            zip(
                planned_columns,
                actual_columns,
                planned_column_candidates,
                actual_column_candidates,
                strict=True,
            ),
            start=1,
        ):
            if expected_candidates.isdisjoint(actual_candidates):
                issues.append(
                    f"output_column_mismatch:{index}:{expected}!={actual}"
                )

    row_count = len(rows)
    if row_count == 0:
        issues.append("unexpected_zero_rows")
    if plan.limit is not None and row_count > plan.limit:
        issues.append(f"row_limit_exceeded:{row_count}>{plan.limit}")
    if plan.requires_aggregation and not plan.requires_grouping and row_count != 1:
        issues.append(f"aggregate_row_count_mismatch:expected=1:actual={row_count}")

    grain_columns: list[str] = []
    for grain_key in plan.grain_keys:
        normalized = _column_name(grain_key)
        if normalized in actual_columns and normalized not in grain_columns:
            grain_columns.append(normalized)
    if grain_columns and row_count > 1:
        seen: set[tuple[str, ...]] = set()
        duplicate = False
        for row in rows:
            normalized_row = {_column_name(key): value for key, value in row.items()}
            key = tuple(repr(normalized_row.get(column)) for column in grain_columns)
            if key in seen:
                duplicate = True
                break
            seen.add(key)
        if duplicate:
            issues.append(f"duplicate_result_grain:{','.join(grain_columns)}")

    contract = context_metadata.get("semantic_contract_validation")
    if isinstance(contract, dict) and str(contract.get("mode") or "") == "required":
        if contract.get("passed") is not True:
            issues.append("semantic_contract_not_validated")

    status = "passed"
    if issues:
        status = "inconclusive" if issues == ["unexpected_zero_rows"] else "failed"

    return ResultValidationCheck(
        status=status,
        issues=issues,
        evidence={
            "row_count": row_count,
            "column_count": len(actual_columns),
            "planned_output_count": len(planned_columns),
            "grain_columns_checked": grain_columns,
            "semantic_contract_mode": (
                str(contract.get("mode") or "")
                if isinstance(contract, dict)
                else "unspecified"
            ),
        },
    )
