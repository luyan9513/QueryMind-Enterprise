from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.evaluation_cli import (  # noqa: E402
    _pricing_snapshot,
    build_evaluator_names,
    should_include_expected_outcome,
)


def test_build_evaluator_names_supports_sql_accuracy_only() -> None:
    assert build_evaluator_names(include_expected_outcome=False) == ["sql_accuracy"]
    assert build_evaluator_names(include_expected_outcome=True) == [
        "sql_accuracy",
        "expected_outcome",
    ]


def test_should_include_expected_outcome_respects_flag_and_env(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_SKIP_EXPECTED_OUTCOME", raising=False)

    assert should_include_expected_outcome(SimpleNamespace(skip_expected_outcome=False)) is True
    assert should_include_expected_outcome(SimpleNamespace(skip_expected_outcome=True)) is False

    monkeypatch.setenv("EVAL_SKIP_EXPECTED_OUTCOME", "true")
    assert should_include_expected_outcome(SimpleNamespace(skip_expected_outcome=False)) is False


def test_pricing_snapshot_requires_complete_explicit_prices(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_INPUT_CACHE_HIT_USD_PER_MILLION", "0.1")
    assert _pricing_snapshot("agent", "deepseek", "demo") is None

    monkeypatch.setenv("EVAL_AGENT_INPUT_CACHE_MISS_USD_PER_MILLION", "1.0")
    monkeypatch.setenv("EVAL_AGENT_OUTPUT_USD_PER_MILLION", "2.0")
    monkeypatch.setenv("EVAL_PRICE_SOURCE_URL", "https://example.com/pricing")
    monkeypatch.setenv("EVAL_PRICE_CHECKED_AT", "2026-07-15")

    snapshot = _pricing_snapshot("agent", "deepseek", "demo")

    assert snapshot is not None
    assert snapshot["model"] == "demo"
    assert snapshot["output_usd_per_million"] == 2.0
    assert snapshot["checked_at"] == "2026-07-15"
