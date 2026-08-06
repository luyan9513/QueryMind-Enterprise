from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.evaluation_cli import (  # noqa: E402
    _pricing_snapshot,
    build_evaluator_names,
    select_test_cases,
    selected_dataset_hash,
    should_include_expected_outcome,
)
from QueryMind.core.evaluation import EvaluationDataset  # noqa: E402


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


def test_select_test_cases_preserves_requested_order_and_rejects_unknown() -> None:
    dataset = EvaluationDataset(
        name="demo",
        test_cases=[
            SimpleNamespace(id="case_a"),
            SimpleNamespace(id="case_b"),
        ],
    )

    selected = select_test_cases(dataset, ["case_b", "case_a", "case_b"])

    assert [case.id for case in selected.test_cases] == ["case_b", "case_a"]
    try:
        select_test_cases(dataset, ["missing"])
    except ValueError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unknown case IDs should fail")


def test_selected_dataset_hash_separates_targeted_runs(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.yaml"
    dataset_path.write_text("dataset: {}\n", encoding="utf-8")

    full_hash = selected_dataset_hash(dataset_path, None)
    smoke_hash = selected_dataset_hash(dataset_path, ["case_a"])

    assert smoke_hash != full_hash
    assert selected_dataset_hash(dataset_path, ["case_a"]) == smoke_hash
