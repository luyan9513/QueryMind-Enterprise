"""Stable execution modes used by Text2SQL A/B evaluations."""

from __future__ import annotations

from QueryMind.core._compat import StrEnum


class EvaluationMode(StrEnum):
    """One isolated evaluation strategy.

    The values are persisted in checkpoints and reports, so they should remain
    stable even if the user-facing labels change.
    """

    S0_SINGLE_SHOT = "s0_single_shot"
    S1_AGENT_WITHOUT_PLAN = "s1_agent_without_plan"
    S2_AGENT_WITH_PLAN = "s2_agent_with_plan"
    S3_ADAPTIVE_AGENT = "s3_adaptive_agent"
    S4_REVIEWED_AGENT = "s4_reviewed_agent"

    @property
    def short_name(self) -> str:
        return self.value.split("_", 1)[0]


_MODE_ALIASES = {
    "s0": EvaluationMode.S0_SINGLE_SHOT,
    "single_shot": EvaluationMode.S0_SINGLE_SHOT,
    "s0_single_shot": EvaluationMode.S0_SINGLE_SHOT,
    "s1": EvaluationMode.S1_AGENT_WITHOUT_PLAN,
    "agent_without_plan": EvaluationMode.S1_AGENT_WITHOUT_PLAN,
    "s1_agent_without_plan": EvaluationMode.S1_AGENT_WITHOUT_PLAN,
    "s2": EvaluationMode.S2_AGENT_WITH_PLAN,
    "agent_with_plan": EvaluationMode.S2_AGENT_WITH_PLAN,
    "s2_agent_with_plan": EvaluationMode.S2_AGENT_WITH_PLAN,
    "s3": EvaluationMode.S3_ADAPTIVE_AGENT,
    "adaptive_agent": EvaluationMode.S3_ADAPTIVE_AGENT,
    "s3_adaptive_agent": EvaluationMode.S3_ADAPTIVE_AGENT,
    "s4": EvaluationMode.S4_REVIEWED_AGENT,
    "reviewed_agent": EvaluationMode.S4_REVIEWED_AGENT,
    "s4_reviewed_agent": EvaluationMode.S4_REVIEWED_AGENT,
}


def parse_evaluation_mode(value: str | EvaluationMode | None) -> EvaluationMode:
    """Normalize CLI/env aliases into one persisted evaluation mode."""
    if isinstance(value, EvaluationMode):
        return value
    normalized = str(value or "s2").strip().lower().replace("-", "_")
    try:
        return _MODE_ALIASES[normalized]
    except KeyError as exc:
        allowed = "s0, s1, s2, s3, s4"
        raise ValueError(f"Evaluation mode must be one of: {allowed}") from exc


__all__ = ["EvaluationMode", "parse_evaluation_mode"]
