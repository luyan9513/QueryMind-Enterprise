"""Dataset coverage checks for production-candidate Text2SQL benchmarks."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .dataset import EvaluationDataset
from .report import ComparisonReport, EvaluationReport


class BenchmarkQualityThresholds(BaseModel):
    """Quality gates applied after repeated model runs are available."""

    reference_sql_success_rate: float = Field(ge=0, le=1)
    schema_recall: float = Field(ge=0, le=1)
    business_accuracy: float = Field(ge=0, le=1)
    wrong_executed_rate: float = Field(ge=0, le=1)
    automatic_answer_coverage: float = Field(ge=0, le=1)
    p95_agent_execution_time_ms: float = Field(gt=0)
    repeat_consistency_rate: float = Field(ge=0, le=1)
    process_trace_coverage: float = Field(default=0.95, ge=0, le=1)


class BenchmarkAdmissionProfile(BaseModel):
    """Versioned coverage and quality requirements for one data source."""

    name: str
    database_id: str
    minimum_cases: int = Field(ge=1)
    required_repeats: int = Field(ge=1)
    minimum_by_difficulty: dict[str, int] = Field(default_factory=dict)
    minimum_by_category: dict[str, int] = Field(default_factory=dict)
    minimum_by_business_domain: dict[str, int] = Field(default_factory=dict)
    minimum_by_split: dict[str, int] = Field(default_factory=dict)
    minimum_by_tag: dict[str, int] = Field(default_factory=dict)
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
    distributions: dict[str, dict[str, int]]
    deficits: list[BenchmarkCoverageDeficit]
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


class BenchmarkQualityGate(BaseModel):
    metric: str
    actual: float
    threshold: float
    operator: str
    passed: bool


class BenchmarkCaseStability(BaseModel):
    """Repeated business-correctness outcome for one frozen benchmark case."""

    case_id: str
    benchmark_split: str
    correct_repeats: int
    total_repeats: int
    status: str


class BenchmarkSplitQuality(BaseModel):
    """Quality metrics scoped to one frozen benchmark split."""

    case_count: int
    sample_count: int
    business_accuracy: float
    wrong_executed_rate: float
    repeat_consistency_rate: float


class BenchmarkQualityAssessment(BaseModel):
    """Quality and stability decision over repeated frozen benchmark runs."""

    profile_name: str
    database_id: str
    report_count: int
    case_count_per_report: int
    metrics: dict[str, float]
    gates: list[BenchmarkQualityGate]
    case_stability: list[BenchmarkCaseStability] = Field(default_factory=list)
    stable_incorrect_case_ids: list[str] = Field(default_factory=list)
    flaky_case_ids: list[str] = Field(default_factory=list)
    split_metrics: dict[str, BenchmarkSplitQuality] = Field(default_factory=dict)
    comparability_issues: list[str] = Field(default_factory=list)
    quality_ready: bool

    def to_markdown(self) -> str:
        lines = [
            f"# Benchmark Quality Admission: {self.profile_name}",
            "",
            f"- Database: `{self.database_id}`",
            f"- Reports: {self.report_count}",
            f"- Cases per report: {self.case_count_per_report}",
            f"- Status: **{'READY' if self.quality_ready else 'NOT READY'}**",
            f"- Comparability issues: {len(self.comparability_issues)}",
            "",
            "| Metric | Actual | Gate | Passed |",
            "|---|---:|---:|:---:|",
        ]
        for gate in self.gates:
            lines.append(
                f"| {gate.metric} | {gate.actual:.4f} | "
                f"{gate.operator} {gate.threshold:.4f} | "
                f"{'yes' if gate.passed else 'no'} |"
            )
        if self.comparability_issues:
            lines.extend(["", "## Comparability issues", ""])
            lines.extend(f"- `{issue}`" for issue in self.comparability_issues)
        lines.extend(
            [
                "",
                "## Failure stability ledger",
                "",
                "- Stable incorrect: "
                + (", ".join(f"`{value}`" for value in self.stable_incorrect_case_ids) or "none"),
                "- Flaky: "
                + (", ".join(f"`{value}`" for value in self.flaky_case_ids) or "none"),
                "",
                "## Metrics by benchmark split",
                "",
                "| Split | Cases | Samples | Business accuracy | Wrong executed | Consistency |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        lines.extend(
            f"| {split} | {metrics.case_count} | {metrics.sample_count} | "
            f"{metrics.business_accuracy:.4f} | {metrics.wrong_executed_rate:.4f} | "
            f"{metrics.repeat_consistency_rate:.4f} |"
            for split, metrics in self.split_metrics.items()
        )
        return "\n".join(lines) + "\n"

    def save_json(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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


def _counter(values: list[str]) -> dict[str, int]:
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
        "split": _counter(
            [str(case.metadata.get("benchmark_split") or "") for case in dataset.test_cases]
        ),
        "tag": _counter([tag for case in dataset.test_cases for tag in case.tags]),
    }

    deficits: list[BenchmarkCoverageDeficit] = []
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
        "split": profile.minimum_by_split,
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


def assess_benchmark_quality(
    reports: list[EvaluationReport],
    profile: BenchmarkAdmissionProfile,
    *,
    reference_sql_success_rate: float,
) -> BenchmarkQualityAssessment:
    """Apply production-candidate gates to comparable repeated reports."""

    if len(reports) < profile.required_repeats:
        raise ValueError(
            f"Expected at least {profile.required_repeats} reports, got {len(reports)}"
        )
    result_maps = [
        {result.test_case.id: result for result in report.results}
        for report in reports
    ]
    if any(
        len(result_map) != len(report.results)
        for result_map, report in zip(result_maps, reports, strict=True)
    ):
        raise ValueError("Repeated reports must not contain duplicate case IDs")
    case_id_sets = [set(result_map) for result_map in result_maps]
    if not case_id_sets[0] or any(
        case_ids != case_id_sets[0] for case_ids in case_id_sets[1:]
    ):
        raise ValueError("Repeated reports must contain the same case IDs")
    canonical_case_ids = sorted(case_id_sets[0])
    if any(
        result.database_id != profile.database_id
        for report in reports
        for result in report.results
    ):
        raise ValueError("Report database IDs do not match the admission profile")

    comparison = ComparisonReport(
        reports={f"repeat_{index}": report for index, report in enumerate(reports, 1)}
    )
    comparability_issues = comparison.comparability_issues()

    samples = [result for report in reports for result in report.results]
    case_stability: list[BenchmarkCaseStability] = []
    for case_id in canonical_case_ids:
        repeated_results = [result_map[case_id] for result_map in result_maps]
        correct_repeats = sum(
            bool(result.metadata.get("business_result_correct"))
            for result in repeated_results
        )
        if correct_repeats == len(repeated_results):
            status = "stable_correct"
        elif correct_repeats == 0:
            status = "stable_incorrect"
        else:
            status = "flaky"
        case_stability.append(
            BenchmarkCaseStability(
                case_id=case_id,
                benchmark_split=str(
                    repeated_results[0].test_case.metadata.get("benchmark_split")
                    or "unspecified"
                ),
                correct_repeats=correct_repeats,
                total_repeats=len(repeated_results),
                status=status,
            )
        )

    split_metrics: dict[str, BenchmarkSplitQuality] = {}
    split_names = sorted({item.benchmark_split for item in case_stability})
    for split in split_names:
        split_case_ids = {
            item.case_id for item in case_stability if item.benchmark_split == split
        }
        split_results = [
            result
            for result_map in result_maps
            for case_id, result in result_map.items()
            if case_id in split_case_ids
        ]
        split_metrics[split] = BenchmarkSplitQuality(
            case_count=len(split_case_ids),
            sample_count=len(split_results),
            business_accuracy=sum(
                bool(result.metadata.get("business_result_correct"))
                for result in split_results
            )
            / len(split_results),
            wrong_executed_rate=sum(
                bool(result.metadata.get("agent_sql_execution_success"))
                and not bool(result.metadata.get("business_result_correct"))
                for result in split_results
            )
            / len(split_results),
            repeat_consistency_rate=sum(
                item.status != "flaky"
                for item in case_stability
                if item.benchmark_split == split
            )
            / len(split_case_ids),
        )

    metrics: dict[str, float] = {
        "reference_sql_success_rate": reference_sql_success_rate,
        "schema_recall": sum(report.average_schema_recall() for report in reports)
        / len(reports),
        "business_accuracy": sum(
            report.business_result_correct_rate() for report in reports
        )
        / len(reports),
        "wrong_executed_rate": sum(report.wrong_executed_rate() for report in reports)
        / len(reports),
        "automatic_answer_coverage": sum(
            report.automatic_answer_coverage() for report in reports
        )
        / len(reports),
        "p95_agent_execution_time_ms": max(
            report.p95_agent_execution_time() for report in reports
        ),
        "repeat_consistency_rate": sum(
            len(
                {
                    bool(result_map[case_id].metadata.get("business_result_correct"))
                    for result_map in result_maps
                }
            )
            == 1
            for case_id in canonical_case_ids
        )
        / len(canonical_case_ids),
        "process_trace_coverage": sum(
            bool(result.agent_result.metadata.get("process_event_count"))
            for result in samples
        )
        / len(samples),
    }

    thresholds = profile.quality_thresholds
    rules: list[tuple[str, float, str]] = [
        ("reference_sql_success_rate", thresholds.reference_sql_success_rate, ">="),
        ("schema_recall", thresholds.schema_recall, ">="),
        ("business_accuracy", thresholds.business_accuracy, ">="),
        ("wrong_executed_rate", thresholds.wrong_executed_rate, "<="),
        ("automatic_answer_coverage", thresholds.automatic_answer_coverage, ">="),
        (
            "p95_agent_execution_time_ms",
            thresholds.p95_agent_execution_time_ms,
            "<=",
        ),
        ("repeat_consistency_rate", thresholds.repeat_consistency_rate, ">="),
        ("process_trace_coverage", thresholds.process_trace_coverage, ">="),
    ]
    gates = [
        BenchmarkQualityGate(
            metric=name,
            actual=metrics[name],
            threshold=threshold,
            operator=operator,
            passed=(metrics[name] >= threshold if operator == ">=" else metrics[name] <= threshold),
        )
        for name, threshold, operator in rules
    ]
    return BenchmarkQualityAssessment(
        profile_name=profile.name,
        database_id=profile.database_id,
        report_count=len(reports),
        case_count_per_report=len(canonical_case_ids),
        metrics=metrics,
        gates=gates,
        case_stability=case_stability,
        stable_incorrect_case_ids=[
            item.case_id for item in case_stability if item.status == "stable_incorrect"
        ],
        flaky_case_ids=[
            item.case_id for item in case_stability if item.status == "flaky"
        ],
        split_metrics=split_metrics,
        comparability_issues=comparability_issues,
        quality_ready=(
            not comparability_issues and all(gate.passed for gate in gates)
        ),
    )
