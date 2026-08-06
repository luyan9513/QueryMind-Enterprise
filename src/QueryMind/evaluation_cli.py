"""QueryMind package entry point for evaluation runs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path

from QueryMind.core.evaluation import (
    DictEvaluationRuntimeResolver,
    EvaluationDataset,
    EvaluationMode,
    EvaluationRunner,
    ExpectedOutcomeEvaluator,
    SqlAccuracyEvaluator,
)

from evals.bootstrap import (
    DEFAULT_DATASET_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_RESUME_ROOT,
    REPO_ROOT,
    TqdmProgressReporter,
    build_llm_service,
    build_recovery_strategy,
    build_runtime_from_env,
    configure_logging,
    dataset_hash,
    load_environment,
    resolve_env_path,
    resolve_evaluation_mode,
    resolve_evaluation_providers,
    should_show_progress,
)
from evals.reporting import save_report_artifacts
from evals.resume_store import EvaluationRunStore
from tqdm import tqdm

logger = logging.getLogger(__name__)


def emit_status(message: str) -> None:
    if should_show_progress():
        tqdm.write(message, file=sys.stderr)
    else:
        print(message, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run QueryMind SQL evaluation")
    parser.add_argument(
        "--dataset-path",
        help="Dataset YAML/JSON file path (defaults to EVAL_DATASET_PATH or basic.yaml)",
    )
    parser.add_argument(
        "--resume-root",
        default=str(DEFAULT_RESUME_ROOT),
        help="Directory used to store resume points",
    )
    parser.add_argument("--run-id", help="Explicit run id for a fresh evaluation run")
    parser.add_argument(
        "--resume-run-id",
        help="Resume a specific run id from the resume root",
    )
    parser.add_argument(
        "--resume-latest",
        action="store_true",
        help="Resume the most recent incomplete run for this dataset",
    )
    parser.add_argument(
        "--report-output-dir",
        help=(
        "Directory for generated reports (defaults to EVAL_OUTPUT_DIR or "
            "eval_output/eval_results/<run_id>_<model>)"
        ),
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip auto-generating reports at the end of the run",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        help="Override EVAL_MAX_CONCURRENCY for the runner",
    )
    parser.add_argument(
        "--provider",
        help="Override the LLM provider for both agent and judge (deepseek or minimax)",
    )
    parser.add_argument(
        "--evaluation-mode",
        choices=["s0", "s1", "s2", "s3", "s4", "s5"],
        help=(
            "Evaluation strategy: s0 single-shot, s1 Agent without Query Plan, "
            "s2 mandatory plan, s3 adaptive routing, or s4 adaptive routing "
            "with structured recovery and high-risk SQL review, or s5 mandatory "
            "plans with required data-source semantic contracts "
            "(defaults to EVAL_MODE or s2)"
        ),
    )
    parser.add_argument(
        "--skip-expected-outcome",
        action="store_true",
        help="Only run sql_accuracy and skip expected_outcome checks",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only the selected test case ID; repeat for multiple cases",
    )
    return parser.parse_args()


def resolve_dataset_path(args: argparse.Namespace) -> Path:
    if args.dataset_path:
        path = Path(args.dataset_path).expanduser()
        if not path.is_absolute():
            return (REPO_ROOT / path).resolve()
        return path
    return resolve_env_path("EVAL_DATASET_PATH", DEFAULT_DATASET_PATH)


def load_dataset(dataset_path: Path) -> EvaluationDataset:
    if dataset_path.suffix.lower() in {".yaml", ".yml"}:
        return EvaluationDataset.from_yaml(dataset_path)
    return EvaluationDataset.from_json(dataset_path)


def select_test_cases(
    dataset: EvaluationDataset,
    case_ids: list[str] | None,
) -> EvaluationDataset:
    """Return a stable, validated subset for smoke and targeted regression runs."""
    if not case_ids:
        return dataset

    requested = list(dict.fromkeys(case_ids))
    cases_by_id = {case.id: case for case in dataset.test_cases}
    missing = [case_id for case_id in requested if case_id not in cases_by_id]
    if missing:
        raise ValueError("Unknown evaluation case ID(s): " + ", ".join(missing))

    return EvaluationDataset(
        name=f"{dataset.name} ({len(requested)} selected cases)",
        test_cases=[cases_by_id[case_id] for case_id in requested],
        description=dataset.description,
    )


def selected_dataset_hash(dataset_path: Path, case_ids: list[str] | None) -> str:
    """Keep full and targeted runs in separate resume namespaces."""
    base_hash = dataset_hash(dataset_path)
    if not case_ids:
        return base_hash
    selection = ",".join(dict.fromkeys(case_ids))
    return hashlib.sha256(f"{base_hash}:{selection}".encode("utf-8")).hexdigest()


def should_include_expected_outcome(args: argparse.Namespace) -> bool:
    env_value = os.getenv("EVAL_SKIP_EXPECTED_OUTCOME", "false").strip().lower()
    if args.skip_expected_outcome or env_value in {"1", "true", "on", "yes"}:
        return False
    return True


def build_evaluator_names(*, include_expected_outcome: bool) -> list[str]:
    names = ["sql_accuracy"]
    if include_expected_outcome:
        names.append("expected_outcome")
    return names


def build_evaluators(
    *,
    runtime_resolver: DictEvaluationRuntimeResolver,
    judge_llm,
    pass_threshold: float,
    preview_rows: int,
    allow_write_sql: bool,
    include_expected_outcome: bool,
):
    evaluators = [
        SqlAccuracyEvaluator(
            runtime_resolver=runtime_resolver,
            judge_llm=judge_llm,
            pass_threshold=pass_threshold,
            preview_rows=preview_rows,
            allow_write_sql=allow_write_sql,
        )
    ]
    if include_expected_outcome:
        evaluators.append(ExpectedOutcomeEvaluator())
    return evaluators


def _pricing_snapshot(role: str, provider: str, model: object) -> dict[str, object] | None:
    prefix = f"EVAL_{role.upper()}"
    fields = {
        "input_cache_hit_usd_per_million": os.getenv(
            f"{prefix}_INPUT_CACHE_HIT_USD_PER_MILLION"
        ),
        "input_cache_miss_usd_per_million": os.getenv(
            f"{prefix}_INPUT_CACHE_MISS_USD_PER_MILLION"
        ),
        "output_usd_per_million": os.getenv(
            f"{prefix}_OUTPUT_USD_PER_MILLION"
        ),
    }
    if not all(value not in (None, "") for value in fields.values()):
        return None
    return {
        "provider": provider,
        "model": str(model or "unknown"),
        **{key: float(value) for key, value in fields.items()},
        "source_url": os.getenv("EVAL_PRICE_SOURCE_URL", ""),
        "checked_at": os.getenv("EVAL_PRICE_CHECKED_AT", ""),
    }


def _build_config_snapshot(
    *,
    dataset_path: Path,
    dataset_hash_value: str,
    runtime,
    judge_llm,
    agent_provider: str,
    judge_provider: str,
    pass_threshold: float,
    preview_rows: int,
    max_concurrency: int,
    allow_write_sql: bool,
    evaluator_names: list[str],
    include_expected_outcome: bool,
    evaluation_mode: EvaluationMode,
) -> dict[str, object]:
    recovery = build_recovery_strategy()
    agent_model = getattr(runtime.agent_llm_service, "model", None)
    judge_model = getattr(judge_llm, "model", None)
    return {
        "dataset_path": str(dataset_path),
        "dataset_hash": dataset_hash_value,
        "database_id": runtime.database_id,
        "database_snapshot_id": os.getenv("EVAL_DATABASE_SNAPSHOT_ID", ""),
        "dialect": runtime.dialect,
        "schema_sync_mode": runtime.schema_sync_mode,
        "schema_snapshot_id": os.getenv("EVAL_SCHEMA_SNAPSHOT_ID", ""),
        "schema_search_default_limit": (
            runtime.agent_config.schema_search_default_limit
        ),
        "schema_search_default_threshold": (
            runtime.agent_config.schema_search_default_threshold
        ),
        "schema_search_default_mode": (
            runtime.agent_config.schema_search_default_mode
        ),
        "allow_write_sql": allow_write_sql,
        "agent_model": agent_model,
        "agent_provider": agent_provider,
        "agent_temperature": runtime.agent_config.temperature,
        "max_tool_iterations": runtime.agent_config.max_tool_iterations,
        "max_metadata_query_retries": (
            runtime.agent_config.max_metadata_query_retries
        ),
        "max_query_plan_retries": runtime.agent_config.max_query_plan_retries,
        "require_query_plan": runtime.agent_config.require_query_plan,
        "query_plan_mode": (
            runtime.agent_config.effective_query_plan_mode().value
        ),
        "structured_failure_recovery": (
            runtime.agent_config.structured_failure_recovery
        ),
        "max_same_failure_retries": runtime.agent_config.max_same_failure_retries,
        "sql_review_mode": runtime.agent_config.effective_sql_review_mode().value,
        "semantic_contract_mode": (
            runtime.agent_config.effective_semantic_contract_mode().value
        ),
        "semantic_contract_data_source_id": (
            runtime.semantic_contract_catalog.data_source_id
            if runtime.semantic_contract_catalog is not None
            else None
        ),
        "semantic_contract_version": (
            runtime.semantic_contract_catalog.version
            if runtime.semantic_contract_catalog is not None
            else None
        ),
        "semantic_contract_hash": (
            runtime.semantic_contract_catalog.fingerprint()
            if runtime.semantic_contract_catalog is not None
            else None
        ),
        "judge_model": judge_model,
        "judge_provider": judge_provider,
        "pass_threshold": pass_threshold,
        "preview_rows": preview_rows,
        "max_concurrency": max_concurrency,
        "evaluator_names": evaluator_names,
        "include_expected_outcome": include_expected_outcome,
        "evaluation_mode": evaluation_mode.value,
        "evaluator_scope": "full" if include_expected_outcome else "sql_accuracy_only",
        "pricing": {
            "agent": _pricing_snapshot("agent", agent_provider, agent_model),
            "judge": _pricing_snapshot("judge", judge_provider, judge_model),
        },
        "recovery": {
            "max_retries": recovery.max_retries,
            "base_delay_ms": recovery.base_delay_ms,
            "max_delay_ms": recovery.max_delay_ms,
            "include_jitter": recovery.include_jitter,
        },
    }


def _resolve_run_store(
    args: argparse.Namespace,
    *,
    resume_root: Path,
    dataset_path: Path,
    dataset_name: str,
    dataset_description: str,
    dataset_hash_value: str,
    evaluator_names: list[str],
    total_test_cases: int,
    evaluation_mode: EvaluationMode,
) -> EvaluationRunStore:
    if args.resume_run_id and args.run_id and args.resume_run_id != args.run_id:
        raise ValueError("--run-id and --resume-run-id cannot point to different runs")

    if args.resume_run_id:
        store = EvaluationRunStore.open_existing(resume_root / args.resume_run_id)
        if store.checkpoint.dataset_hash != dataset_hash_value:
            raise ValueError(
                "Resume run dataset hash does not match the current dataset. "
                "Use the same dataset to resume."
            )
        if list(store.checkpoint.evaluator_names or []) != evaluator_names:
            raise ValueError(
                "Resume run evaluator configuration does not match the current evaluation mode."
            )
        stored_mode = store.checkpoint.config_snapshot.get("evaluation_mode")
        if stored_mode != evaluation_mode.value:
            raise ValueError(
                "Resume run evaluation mode does not match the requested mode."
            )
        return store

    if args.resume_latest:
        store = EvaluationRunStore.find_latest(
            resume_root,
            dataset_hash=dataset_hash_value,
            evaluator_names=evaluator_names,
            config_filters={"evaluation_mode": evaluation_mode.value},
            only_incomplete=True,
        )
        if store is None:
            raise FileNotFoundError(
                f"No incomplete resume point found in {resume_root} for this dataset "
                "and evaluator configuration"
            )
        if store.checkpoint.dataset_hash != dataset_hash_value:
            raise ValueError(
                "Latest resume point dataset hash does not match the current dataset"
            )
        if list(store.checkpoint.evaluator_names or []) != evaluator_names:
            raise ValueError(
                "Latest resume point evaluator configuration does not match the current evaluation mode."
            )
        return store

    return EvaluationRunStore.create_new(
        resume_root,
        dataset_path=dataset_path,
        dataset_hash=dataset_hash_value,
        dataset_name=dataset_name,
        dataset_description=dataset_description,
        total_test_cases=total_test_cases,
        evaluator_names=evaluator_names,
        config_snapshot={"evaluation_mode": evaluation_mode.value},
        run_id=args.run_id,
    )


async def main_async(args: argparse.Namespace) -> None:
    load_environment()

    evaluation_mode = resolve_evaluation_mode(args.evaluation_mode)

    agent_provider, judge_provider = resolve_evaluation_providers(args.provider)
    logger.info(
        "Evaluation LLM providers resolved: agent=%s judge=%s",
        agent_provider,
        judge_provider,
    )

    dataset_path = resolve_dataset_path(args)
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {dataset_path}. Set EVAL_DATASET_PATH or --dataset-path."
        )

    emit_status("▶ Loading evaluation dataset...")
    dataset = select_test_cases(load_dataset(dataset_path), args.case_ids)
    emit_status(f"✅ Dataset loaded: {dataset.name} ({len(dataset.test_cases)} cases)")

    resume_root = Path(args.resume_root).expanduser()
    dataset_hash_value = selected_dataset_hash(dataset_path, args.case_ids)
    include_expected_outcome = should_include_expected_outcome(args)
    evaluator_names = build_evaluator_names(
        include_expected_outcome=include_expected_outcome,
    )

    emit_status("▶ Resolving resume point...")
    store = _resolve_run_store(
        args,
        resume_root=resume_root,
        dataset_path=dataset_path,
        dataset_name=dataset.name,
        dataset_description=dataset.description,
        dataset_hash_value=dataset_hash_value,
        evaluator_names=evaluator_names,
        total_test_cases=len(dataset.test_cases),
        evaluation_mode=evaluation_mode,
    )

    configure_logging(store.log_path)
    logger.info("Evaluation resume root: %s", resume_root)
    logger.info("Evaluation run id: %s", store.checkpoint.run_id)

    emit_status(
        f"✅ Resume point ready: {store.checkpoint.run_id} "
        f"(status={store.checkpoint.status})"
    )

    emit_status("▶ Building evaluation runtime...")
    runtime = build_runtime_from_env(
        provider=agent_provider,
        evaluation_mode=evaluation_mode,
    )
    emit_status(
        "✅ Evaluation runtime ready "
        f"(evaluation mode: {evaluation_mode.value}, "
        f"schema mode: {runtime.schema_sync_mode}, "
        f"max tool iterations: {runtime.agent_config.max_tool_iterations})"
    )
    resolver = DictEvaluationRuntimeResolver({runtime.database_id: runtime})

    emit_status("▶ Initializing agent and judge LLM services...")
    recovery_strategy = build_recovery_strategy()
    judge_llm = build_llm_service(
        "EVAL_JUDGE",
        recovery_strategy=recovery_strategy,
        provider=judge_provider,
    )
    emit_status("✅ Agent/Judge LLM services ready")

    pass_threshold = float(os.getenv("EVAL_PASS_THRESHOLD", "0.7"))
    preview_rows = int(os.getenv("EVAL_PREVIEW_ROWS", "10"))
    max_concurrency = args.max_concurrency or int(os.getenv("EVAL_MAX_CONCURRENCY", "2"))
    allow_write_sql = os.getenv("EVAL_ALLOW_WRITE_SQL", "false").lower() == "true"

    evaluators = build_evaluators(
        runtime_resolver=resolver,
        judge_llm=judge_llm,
        pass_threshold=pass_threshold,
        preview_rows=preview_rows,
        allow_write_sql=allow_write_sql,
        include_expected_outcome=include_expected_outcome,
    )

    store.checkpoint.evaluator_names = evaluator_names
    store.checkpoint.total_test_cases = len(dataset.test_cases)
    store.checkpoint.config_snapshot = _build_config_snapshot(
        dataset_path=dataset_path,
        dataset_hash_value=dataset_hash_value,
        runtime=runtime,
        judge_llm=judge_llm,
        agent_provider=agent_provider,
        judge_provider=judge_provider,
        pass_threshold=pass_threshold,
        preview_rows=preview_rows,
        max_concurrency=max_concurrency,
        allow_write_sql=allow_write_sql,
        evaluator_names=evaluator_names,
        include_expected_outcome=include_expected_outcome,
        evaluation_mode=evaluation_mode,
    )
    store.hydrate_completed_ids()
    store.mark_status("running")

    completed_ids = store.completed_test_case_ids()
    completed_count = len(completed_ids)
    pending_count = len(dataset.test_cases) - completed_count
    emit_status(
        f"▶ Resume progress: {completed_count}/{len(dataset.test_cases)} complete, "
        f"{pending_count} pending"
    )

    progress = None
    if should_show_progress():
        progress = TqdmProgressReporter()
        progress.set_totals(
            agent_total=len(dataset.test_cases),
            judge_total=len(dataset.test_cases) * len(evaluators),
            evaluator_names=evaluator_names,
            agent_initial=completed_count,
            judge_initial=completed_count * len(evaluators),
        )

    runner = EvaluationRunner(
        evaluators=evaluators,
        runtime_resolver=resolver,
        max_concurrency=max_concurrency,
        progress_callback=progress,
        result_callback=store.append_result,
        skip_test_case_ids=completed_ids,
        progress_initial_completed=completed_count,
    )

    report = None
    run_failed = None
    try:
        if pending_count > 0:
            emit_status("▶ Starting evaluation...")
            await runner.run_evaluation(dataset)
        else:
            emit_status("✅ All test cases already completed; skipping execution")
        store.hydrate_completed_ids()
        if len(store.completed_test_case_ids()) >= len(dataset.test_cases):
            store.mark_status("completed")
        else:
            store.mark_status("partial")
    except KeyboardInterrupt:
        run_failed = "Interrupted by user"
        store.mark_status("partial", error=run_failed)
        raise
    except Exception as exc:
        run_failed = str(exc)
        store.mark_status("partial", error=run_failed)
        raise
    finally:
        if not args.no_report:
            report_root = (
                Path(args.report_output_dir).expanduser()
                if args.report_output_dir
                else resolve_env_path("EVAL_OUTPUT_DIR", DEFAULT_RESULTS_ROOT)
            )
            if report_root.name == "eval_output":
                report_root = report_root / "eval_results"
            output_dir = store.report_output_dir(report_root)
            try:
                report = save_report_artifacts(store, output_dir)
                emit_status(f"✅ Report saved to: {output_dir}")
                report.print_summary()
            except Exception as report_exc:
                logger.exception("Failed to generate report: %s", report_exc)
        if run_failed:
            logger.error("Evaluation ended with error: %s", run_failed)


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
