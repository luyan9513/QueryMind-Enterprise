"""High-risk SQL semantic review models and routing helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from .._compat import StrEnum
from .query_plan import assess_query_plan_risk


class SqlReviewMode(StrEnum):
    DISABLED = "disabled"
    HIGH_RISK = "high_risk"
    ALWAYS = "always"


def parse_sql_review_mode(value: str | SqlReviewMode | None) -> SqlReviewMode:
    if isinstance(value, SqlReviewMode):
        return value
    normalized = str(value or "disabled").strip().lower().replace("-", "_")
    aliases = {
        "off": SqlReviewMode.DISABLED,
        "false": SqlReviewMode.DISABLED,
        "disabled": SqlReviewMode.DISABLED,
        "high_risk": SqlReviewMode.HIGH_RISK,
        "risk_based": SqlReviewMode.HIGH_RISK,
        "on": SqlReviewMode.ALWAYS,
        "true": SqlReviewMode.ALWAYS,
        "always": SqlReviewMode.ALWAYS,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            "SQL review mode must be one of: disabled, high_risk, always"
        ) from exc


def sql_fingerprint(sql: str) -> str:
    normalized = " ".join(str(sql or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def requires_sql_review(
    sql: str,
    context_metadata: dict[str, Any],
    *,
    mode: str | SqlReviewMode,
    dialect: str | None = None,
) -> bool:
    resolved = parse_sql_review_mode(mode)
    if resolved == SqlReviewMode.DISABLED:
        return False
    if resolved == SqlReviewMode.ALWAYS:
        return True
    return assess_query_plan_risk(
        sql,
        context_metadata,
        dialect=dialect,
    ).requires_plan


class SqlIntentReview(BaseModel):
    decision: str = Field(pattern="^(approve|revise|clarify)$")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_tags: list[str] = Field(default_factory=list)
    feedback: str = Field(default="")
    checked_dimensions: list[str] = Field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


def parse_sql_intent_review(content: str) -> SqlIntentReview:
    """Parse a JSON reviewer response, accepting one fenced JSON object."""
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if not candidate.startswith("{"):
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            candidate = match.group(0)
    payload = json.loads(candidate)
    return SqlIntentReview.model_validate(payload)
