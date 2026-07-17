"""Re-score persisted SQL artifacts after evaluator-policy changes.

This command never calls the Agent, Judge, or database. It is safe only when
the stored result artifacts already contain both exact and comparison hashes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from QueryMind.core.evaluation import EvaluationDataset, EvaluationReport, EvaluationResult
from QueryMind.core.evaluation.failure_attribution import enrich_failure_attribution
from QueryMind.core.evaluation.metrics import (
    compare_execution_artifacts,
    enrich_result_metrics,
    evaluate_sql_contract,
    sql_requires_order,
)

from .bootstrap import DEFAULT_RESUME_ROOT, dataset_hash
from .resume_store import EvaluationRunStore


def _assert_policy_hashes_are_reusable(result: EvaluationResult) -> None:
    artifact = result.agent_artifact
    ground_truth = result.ground_truth_artifact
    if artifact is None or ground_truth is None:
        raise ValueError(f"{result.test_case.id}: execution artifacts are missing")
    required = (
        artifact.comparison_ordered_result_fingerprint,
        artifact.comparison_unordered_result_fingerprint,
        ground_truth.comparison_ordered_result_fingerprint,
        ground_truth.comparison_unordered_result_fingerprint,
    )
    if not all(required):
        raise ValueError(
            f"{result.test_case.id}: comparison hashes are missing; re-execute SQL instead"
        )

    previous = result.metadata.get("result_comparison_policy") or {}
    current = result.test_case.result_comparison.model_dump(mode="json")
    for field in ("numeric_decimal_places", "value_aliases"):
        if previous.get(field) != current.get(field):
            raise ValueError(
                f"{result.test_case.id}: {field} changed; stored hashes cannot be reused"
            )


def rescore_result(
    result: EvaluationResult,
    current_test_case,
) -> EvaluationResult:
    """Apply current comparison and SQL-contract rules to stored artifacts."""
    rescored = result.model_copy(deep=True)
    rescored.test_case = current_test_case
    _assert_policy_hashes_are_reusable(rescored)

    order_sensitive = sql_requires_order(
        current_test_case.ground_truth_sql,
        current_test_case.dialect,
    )
    exact = compare_execution_artifacts(
        rescored.agent_artifact,
        rescored.ground_truth_artifact,
        order_sensitive=order_sensitive,
    )
    policy = current_test_case.result_comparison
    business_order_sensitive = (
        policy.order_sensitive
        if policy.order_sensitive is not None
        else order_sensitive
    )
    business = compare_execution_artifacts(
        rescored.agent_artifact,
        rescored.ground_truth_artifact,
        order_sensitive=business_order_sensitive,
        use_comparison_fingerprints=True,
        compare_column_names=policy.compare_column_names,
    )
    contract = evaluate_sql_contract(
        rescored.agent_artifact.sql_text,
        current_test_case.expected_sql_contract,
        dialect=current_test_case.dialect,
    )
    contract_passed = bool(contract["sql_contract_passed"])
    exact_correct = bool(exact["result_correct"] and contract_passed)
    business_correct = bool(business["result_correct"] and contract_passed)
    rescored.metadata.update(
        {
            **exact,
            **contract,
            "verified_result_correct": exact_correct,
            "business_result_correct": business_correct,
            "result_comparison_policy": policy.model_dump(mode="json"),
            "rescored_without_model_or_database": True,
        }
    )

    if exact_correct:
        rescored.passed = True
        rescored.score = 1.0
        rescored.reason = "Full result fingerprint and current SQL contract matched."
        rescored.issue_tags = []
    elif business_correct:
        rescored.passed = True
        rescored.score = 0.95
        rescored.reason = "Current deterministic business comparison policy matched."
        rescored.issue_tags = ["formatting_only"]
    elif not contract_passed:
        rescored.passed = False
        rescored.score = min(rescored.score, 0.5)
        rescored.reason = (
            "Generated SQL violated the current SQL contract: "
            + ", ".join(contract["sql_contract_violations"])
        )
        rescored.issue_tags = ["wrong_semantics"]

    enrich_result_metrics(rescored)
    enrich_failure_attribution(rescored)
    return rescored


def build_rescored_report(
    store: EvaluationRunStore,
    dataset: EvaluationDataset,
    *,
    dataset_path: Path,
) -> EvaluationReport:
    cases = {case.id: case for case in dataset}
    results = []
    for result in store.load_results():
        current = cases.get(result.test_case.id)
        if current is None:
            raise KeyError(f"Missing test case in current dataset: {result.test_case.id}")
        results.append(rescore_result(result, current))

    checkpoint = store.checkpoint
    report = EvaluationReport(
        dataset_name=dataset.name,
        results=results,
        evaluator_names=checkpoint.evaluator_names,
        metadata={
            "run_id": f"{checkpoint.run_id}-rescored",
            "run_status": checkpoint.status,
            "completed_test_cases": len(results),
            "total_test_cases": checkpoint.total_test_cases,
            "dataset_path": str(dataset_path),
            "dataset_hash": dataset_hash(dataset_path),
            "config_snapshot": checkpoint.config_snapshot,
            "rescored_from_run_id": checkpoint.run_id,
            "rescored_without_model_or_database": True,
        },
    )
    report.enrich_metadata()
    return report


def save_report(report: EvaluationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report.save_json(output_dir / "evaluation_report.json")
    report.save_csv(output_dir / "evaluation_report.csv")
    report.save_markdown(output_dir / "evaluation_report.md")
    report.save_detailed_markdown(output_dir / "evaluation_detailed.md")
    report.save_html(output_dir / "evaluation_report.html")


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Re-score a completed evaluation run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--resume-root", default=str(DEFAULT_RESUME_ROOT))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    dataset_path = Path(args.dataset_path).expanduser().resolve()
    dataset = EvaluationDataset.from_yaml(dataset_path)
    store = EvaluationRunStore.open_existing(
        Path(args.resume_root).expanduser() / args.run_id
    )
    save_report(
        build_rescored_report(store, dataset, dataset_path=dataset_path),
        Path(args.output_dir).expanduser(),
    )


if __name__ == "__main__":
    main()
