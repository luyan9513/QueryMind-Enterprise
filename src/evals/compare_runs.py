"""Build a fairness-checked comparison from S0/S1/S2 evaluation reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from QueryMind.core.evaluation import ComparisonReport, EvaluationReport


_REDACTED_LIST_FIELDS = {
    "preview_rows",
    "ground_truth_result_preview",
    "agent_result_preview",
}


def _restore_redacted_lists(value):
    """Make sanitized report JSON loadable without restoring business rows."""
    if isinstance(value, list):
        return [_restore_redacted_lists(item) for item in value]
    if not isinstance(value, dict):
        return value
    restored = {}
    for key, item in value.items():
        if key in _REDACTED_LIST_FIELDS and item == "<omitted>":
            restored[key] = []
        else:
            restored[key] = _restore_redacted_lists(item)
    return restored


def _report_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path / "evaluation_report.json" if path.is_dir() else path


def load_report(value: str) -> EvaluationReport:
    path = _report_path(value)
    if not path.exists():
        raise FileNotFoundError(f"Evaluation report not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvaluationReport.model_validate(_restore_redacted_lists(payload))


def merge_reports(label: str, values: list[str]) -> EvaluationReport:
    reports = [load_report(value) for value in values]
    if not reports:
        raise ValueError(f"No reports supplied for {label}")
    comparison = ComparisonReport(
        reports={f"{label}_r{index}": report for index, report in enumerate(reports, 1)}
    )
    issues = comparison.comparability_issues()
    if issues:
        raise ValueError(
            f"Repeated {label} reports are not comparable: " + ", ".join(issues)
        )
    merged = reports[0].model_copy(deep=True)
    merged.results = [result for report in reports for result in report.results]
    merged.metadata["repeat_count"] = len(reports)
    merged.metadata["source_report_paths"] = [str(_report_path(value)) for value in values]
    merged.enrich_metadata()
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare QueryMind S0/S1/S2 runs")
    parser.add_argument("--s0-report", action="append", required=True)
    parser.add_argument("--s1-report", action="append", required=True)
    parser.add_argument("--s2-report", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repeat_counts = {
        len(args.s0_report),
        len(args.s1_report),
        len(args.s2_report),
    }
    if len(repeat_counts) != 1:
        raise SystemExit("S0, S1, and S2 must provide the same number of reports")
    comparison = ComparisonReport(
        reports={
            "s0": merge_reports("s0", args.s0_report),
            "s1": merge_reports("s1", args.s1_report),
            "s2": merge_reports("s2", args.s2_report),
        }
    )
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison.save_json(output_dir / "evaluation_comparison.json")
    comparison.save_markdown(output_dir / "evaluation_comparison.md")
    if comparison.comparability_issues():
        raise SystemExit(
            "Comparison artifacts were generated, but fairness checks failed: "
            + ", ".join(comparison.comparability_issues())
        )


if __name__ == "__main__":
    main()
