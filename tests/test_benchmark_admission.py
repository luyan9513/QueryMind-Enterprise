from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.core.evaluation import (  # noqa: E402
    BenchmarkAdmissionProfile,
    BenchmarkQualityThresholds,
    EvaluationDataset,
    SqlTestCase,
    assess_benchmark_admission,
    load_benchmark_admission_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def _quality_thresholds() -> BenchmarkQualityThresholds:
    return BenchmarkQualityThresholds(
        reference_sql_success_rate=1.0,
        schema_recall=0.9,
        business_accuracy=0.8,
        wrong_executed_rate=0.1,
        automatic_answer_coverage=0.85,
        p95_agent_execution_time_ms=30000,
        repeat_consistency_rate=0.85,
    )


def test_chinook_production_profile_meets_frozen_coverage_requirements() -> None:
    dataset = EvaluationDataset.from_yaml(
        ROOT / "src/evals/datasets/chinook_business_zh.yaml"
    )
    profile = load_benchmark_admission_profile(
        ROOT / "config/evaluation/chinook_production_admission.yaml"
    )

    assessment = assess_benchmark_admission(dataset, profile)
    assert assessment.case_count == 100
    assert assessment.distributions["difficulty"] == {
        "easy": 30,
        "hard": 30,
        "medium": 40,
    }
    assert assessment.distributions["split"] == {
        "development": 60,
        "holdout": 20,
        "test": 20,
    }
    assert assessment.deficits == []
    assert assessment.coverage_ready is True
    assert assessment.repeat_requirement_met is False
    assert assessment.production_evaluation_ready is False


def test_adventureworks_regression_profile_meets_frozen_coverage_requirements() -> None:
    dataset = EvaluationDataset.from_yaml(
        ROOT / "src/evals/datasets/adventureworks_business_zh.yaml"
    )
    profile = load_benchmark_admission_profile(
        ROOT / "config/evaluation/adventureworks_regression_admission.yaml"
    )

    assessment = assess_benchmark_admission(dataset, profile)

    assert assessment.case_count == 24
    assert assessment.distributions["difficulty"] == {
        "easy": 14,
        "medium": 10,
    }
    assert assessment.distributions["business_domain"] == {
        "customer": 3,
        "human_resources": 2,
        "inventory": 2,
        "product": 5,
        "purchasing": 3,
        "sales": 9,
    }
    assert assessment.deficits == []
    assert assessment.coverage_ready is True
    assert assessment.repeat_requirement_met is False
    assert assessment.production_evaluation_ready is False


def test_benchmark_is_ready_only_after_coverage_and_repeats_pass() -> None:
    dataset = EvaluationDataset(
        name="tiny",
        test_cases=[
            SqlTestCase(
                id="tiny-1",
                database_id="demo",
                dialect="postgres",
                query="count rows",
                ground_truth_sql="SELECT COUNT(*) FROM demo",
                difficulty="easy",
                tags=["aggregation"],
                metadata={"category": "aggregation", "business_domain": "sales"},
            )
        ],
    )
    profile = BenchmarkAdmissionProfile(
        name="tiny-profile",
        database_id="demo",
        minimum_cases=1,
        required_repeats=3,
        minimum_by_difficulty={"easy": 1},
        minimum_by_category={"aggregation": 1},
        minimum_by_business_domain={"sales": 1},
        minimum_by_tag={"aggregation": 1},
        quality_thresholds=_quality_thresholds(),
    )

    before_repeats = assess_benchmark_admission(dataset, profile)
    after_repeats = assess_benchmark_admission(
        dataset,
        profile,
        completed_repeats=3,
    )

    assert before_repeats.coverage_ready is True
    assert before_repeats.production_evaluation_ready is False
    assert after_repeats.production_evaluation_ready is True


def test_benchmark_profile_rejects_another_database() -> None:
    dataset = EvaluationDataset(
        name="wrong-source",
        test_cases=[
            SqlTestCase(
                id="wrong-1",
                database_id="other",
                dialect="postgres",
                query="count rows",
                ground_truth_sql="SELECT 1",
            )
        ],
    )
    profile = BenchmarkAdmissionProfile(
        name="demo-profile",
        database_id="demo",
        minimum_cases=1,
        required_repeats=1,
        quality_thresholds=_quality_thresholds(),
    )

    with pytest.raises(ValueError, match="do not match profile"):
        assess_benchmark_admission(dataset, profile)


def test_dataset_loader_rejects_indirect_include_cycles(tmp_path: Path) -> None:
    case_template = """
  test_cases:
    - id: {case_id}
      database_id: demo
      dialect: postgres
      query: count rows
      ground_truth_sql: SELECT 1
"""
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        "dataset:\n  name: first\n  includes: [second.yaml]\n"
        + case_template.format(case_id="first-1"),
        encoding="utf-8",
    )
    second.write_text(
        "dataset:\n  name: second\n  includes: [first.yaml]\n"
        + case_template.format(case_id="second-1"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="include cycle"):
        EvaluationDataset.from_yaml(first)
