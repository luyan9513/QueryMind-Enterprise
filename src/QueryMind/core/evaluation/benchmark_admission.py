"""Dataset coverage checks for production-candidate Text2SQL benchmarks."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Dict, List

import yaml
from pydantic import BaseModel, Field

from .dataset import EvaluationDataset


class BenchmarkQualityThresholds(BaseModel):
    """Quality gates applied after repeated model runs are available."""

    reference_sql_success_rate: float = Field(ge=0, le=1)
    schema_recall: float = Field(ge=0, le=1)
    business_accuracy: float = Field(ge=0, le=1)
    wrong_executed_rate: float = Field(ge=0, le=1)
    automatic_answer_coverage: float = Field(ge=0, le=1)
    p95_agent_execution_time_ms: float = Field(gt=0)
    repeat_consistency_rate: float = Field(ge=0, le=1)


class BenchmarkAdmissionProfile(BaseModel):
    """Versioned coverage and quality requirements for one data source."""

    name: str
    database_id: str
    minimum_cases: int = Field(ge=1)
    required_repeats: int = Field(ge=1)
    minimum_by_difficulty: Dict[str, int] = Field(default_factory=dict)
    minimum_by_category: Dict[str, int] = Field(default_factory=dict)
    minimum_by_business_domain: Dict[str, int] = Field(default_factory=dict)
    minimum_by_tag: Dict[str, int] = Field(default_factory=dict)
    quality_thresholds: BenchmarkQualityThresholds


class BenchmarkCoverageDeficit(BaseModel):
    """One unmet benchmark coverage requirement."""

    dimension: str
    value: str
    actual: int
    required: int

    @property
    def missing(self) -> int:
        return max(self.required - self.actual, 0)


class BenchmarkAdmissionAssessment(BaseModel):
    """Read-only assessment of whether a dataset is ready for repeated runs."""

    profile_name: str
    database_id: str
    dataset_name: str
    case_count: int
    minimum_cases: int
    completed_repeats: int
    required_repeats: int
    distributions: Dict[str, Dict[str, int]]
    deficits: List[BenchmarkCoverageDeficit]
    coverage_ready: bool
    repeat_requirement_met: bool
    production_evaluation_ready: bool
    quality_thresholds: BenchmarkQualityThresholds

    def save_json(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def to_markdown(self) -> str:
        status = "READY" if self.production_evaluation_ready else "NOT READY"
        lines = [
            f"# Benchmark Admission Coverage: {self.dataset_name}",
            "",
            f"- Profile: `{self.profile_name}`",
            f"- Database: `{self.database_id}`",
            f"- Status: **{status}**",
            f"- Cases: {self.case_count}/{self.minimum_cases}",
            f"- Completed repeats: {self.completed_repeats}/{self.required_repeats}",
            "",
            "## Coverage deficits",
            "",
        ]
        if not self.deficits:
            lines.append("No coverage deficits.")
        else:
            lines.extend(
                [
                    "| Dimension | Value | Actual | Required | Missing |",
                    "|---|---|---:|---:|---:|",
                ]
            )
            lines.extend(
                f"| {item.dimension} | {item.value} | {item.actual} | "
                f"{item.required} | {item.missing} |"
                for item in self.deficits
            )
        return "\n".join(lines) + "\n"

    def save_markdown(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_markdown(), encoding="utf-8")


def load_benchmark_admission_profile(
    path: str | Path,
) -> BenchmarkAdmissionProfile:
    """Load a benchmark admission profile from YAML."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return BenchmarkAdmissionProfile.model_validate(payload.get("profile", payload))


def _counter(values: List[str]) -> Dict[str, int]:
    return dict(sorted(Counter(value for value in values if value).items()))


def assess_benchmark_admission(
    dataset: EvaluationDataset,
    profile: BenchmarkAdmissionProfile,
    *,
    completed_repeats: int = 0,
) -> BenchmarkAdmissionAssessment:
    """Measure coverage gaps without calling a model or database."""

    if completed_repeats < 0:
        raise ValueError("completed_repeats must be non-negative")

    database_ids = {case.database_id for case in dataset.test_cases}
    if database_ids != {profile.database_id}:
        raise ValueError(
            "Dataset database IDs do not match profile: "
            f"expected {profile.database_id!r}, got {sorted(database_ids)!r}"
        )

    distributions = {
        "difficulty": _counter([case.difficulty or "" for case in dataset.test_cases]),
        "category": _counter(
            [str(case.metadata.get("category") or "") for case in dataset.test_cases]
        ),
        "business_domain": _counter(
            [
                str(case.metadata.get("business_domain") or "")
                for case in dataset.test_cases
            ]
        ),
        "tag": _counter([tag for case in dataset.test_cases for tag in case.tags]),
    }

    deficits: List[BenchmarkCoverageDeficit] = []
    if len(dataset.test_cases) < profile.minimum_cases:
        deficits.append(
            BenchmarkCoverageDeficit(
                dimension="cases",
                value="total",
                actual=len(dataset.test_cases),
                required=profile.minimum_cases,
            )
        )

    requirements = {
        "difficulty": profile.minimum_by_difficulty,
        "category": profile.minimum_by_category,
        "business_domain": profile.minimum_by_business_domain,
        "tag": profile.minimum_by_tag,
    }
    for dimension, required_counts in requirements.items():
        actual_counts = distributions[dimension]
        for value, required in required_counts.items():
            actual = actual_counts.get(value, 0)
            if actual < required:
                deficits.append(
                    BenchmarkCoverageDeficit(
                        dimension=dimension,
                        value=value,
                        actual=actual,
                        required=required,
                    )
                )

    coverage_ready = not deficits
    repeat_requirement_met = completed_repeats >= profile.required_repeats
    return BenchmarkAdmissionAssessment(
        profile_name=profile.name,
        database_id=profile.database_id,
        dataset_name=dataset.name,
        case_count=len(dataset.test_cases),
        minimum_cases=profile.minimum_cases,
        completed_repeats=completed_repeats,
        required_repeats=profile.required_repeats,
        distributions=distributions,
        deficits=deficits,
        coverage_ready=coverage_ready,
        repeat_requirement_met=repeat_requirement_met,
        production_evaluation_ready=coverage_ready and repeat_requirement_met,
        quality_thresholds=profile.quality_thresholds,
    )
