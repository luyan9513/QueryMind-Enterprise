"""Check benchmark coverage before spending model or database budget."""

from __future__ import annotations

import argparse

from QueryMind.core.evaluation import EvaluationDataset
from QueryMind.core.evaluation.benchmark_admission import (
    assess_benchmark_admission,
    load_benchmark_admission_profile,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--profile-path", required=True)
    parser.add_argument("--completed-repeats", type=int, default=0)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = EvaluationDataset.from_yaml(args.dataset_path)
    profile = load_benchmark_admission_profile(args.profile_path)
    assessment = assess_benchmark_admission(
        dataset,
        profile,
        completed_repeats=args.completed_repeats,
    )
    if args.output_json:
        assessment.save_json(args.output_json)
    if args.output_markdown:
        assessment.save_markdown(args.output_markdown)
    print(assessment.to_markdown())


if __name__ == "__main__":
    main()
