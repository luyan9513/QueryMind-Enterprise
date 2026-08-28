"""Assess repeated real-model reports against production-candidate gates."""

from __future__ import annotations

import argparse

from evals.compare_runs import load_report
from QueryMind.core.evaluation.benchmark_admission import (
    assess_benchmark_quality,
    load_benchmark_admission_profile,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-path", required=True)
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument("--reference-sql-success-rate", type=float, required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = load_benchmark_admission_profile(args.profile_path)
    assessment = assess_benchmark_quality(
        [load_report(value) for value in args.report],
        profile,
        reference_sql_success_rate=args.reference_sql_success_rate,
    )
    if args.output_json:
        assessment.save_json(args.output_json)
    if args.output_markdown:
        assessment.save_markdown(args.output_markdown)
    print(assessment.to_markdown())


if __name__ == "__main__":
    main()
