"""Execute dataset reference SQL without invoking an LLM or schema retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from QueryMind.capabilities.sql_runner import RunSqlToolArgs
from QueryMind.core.evaluation import EvaluationDataset
from QueryMind.core.evaluation.runtime import NoOpAgentMemory
from QueryMind.core.evaluation.sql_policy import is_read_only_sql
from QueryMind.core.tool import ToolContext
from QueryMind.core.user import User

from bootstrap import build_sql_runner_from_env, load_environment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Validate only this case; repeat the option to select more cases.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser.parse_args()


async def validate_reference_queries(dataset: EvaluationDataset, runtime: Any) -> dict:
    database_ids = {case.database_id for case in dataset.test_cases}
    dialects = {case.dialect for case in dataset.test_cases}
    if database_ids != {runtime.database_id}:
        raise ValueError(
            f"Dataset database ids {sorted(database_ids)} do not match runtime "
            f"database id {runtime.database_id!r}"
        )
    if dialects != {runtime.dialect}:
        raise ValueError(
            f"Dataset dialects {sorted(dialects)} do not match runtime dialect "
            f"{runtime.dialect!r}"
        )

    context = ToolContext(
        user=runtime.default_user,
        conversation_id="reference-sql-validation",
        request_id="reference-sql-validation",
        raw_user_message="Validate benchmark reference SQL",
        agent_memory=NoOpAgentMemory(),
    )
    results: list[dict[str, Any]] = []
    for case in dataset.test_cases:
        started = time.perf_counter()
        result: dict[str, Any] = {
            "case_id": case.id,
            "success": False,
            "row_count": 0,
            "column_count": 0,
            "empty_result": False,
            "execution_time_ms": 0.0,
            "error_type": None,
        }
        try:
            if not is_read_only_sql(case.ground_truth_sql):
                raise ValueError("Reference SQL is not read-only")
            frame = await runtime.sql_runner.run_sql(
                RunSqlToolArgs(sql=case.ground_truth_sql),
                context,
            )
            result.update(
                success=True,
                row_count=len(frame.index),
                column_count=len(frame.columns),
                empty_result=frame.empty,
            )
        except Exception as exc:  # report the class without leaking connection values
            result["error_type"] = type(exc).__name__
        finally:
            result["execution_time_ms"] = round(
                (time.perf_counter() - started) * 1000,
                2,
            )
        results.append(result)

    success_count = sum(item["success"] for item in results)
    empty_result_count = sum(
        item["success"] and item["empty_result"] for item in results
    )
    return {
        "dataset_name": dataset.name,
        "database_id": runtime.database_id,
        "dialect": runtime.dialect,
        "case_count": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "empty_result_count": empty_result_count,
        "success_rate": success_count / len(results) if results else 0.0,
        "results": results,
    }


def to_markdown(report: dict) -> str:
    status = "PASS" if report["failure_count"] == 0 else "FAIL"
    lines = [
        f"# Reference SQL Validation: {report['dataset_name']}",
        "",
        f"- Status: **{status}**",
        f"- Database: `{report['database_id']}`",
        f"- Dialect: `{report['dialect']}`",
        f"- Success: {report['success_count']}/{report['case_count']} "
        f"({report['success_rate']:.2%})",
        f"- Empty successful results: {report['empty_result_count']}",
        "",
        "| Case | Success | Rows | Columns | Empty | Time (ms) | Error type |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            "| {case_id} | {success} | {row_count} | {column_count} | "
            "{empty_result} | {execution_time_ms:.2f} | {error_type} |".format(
                **{**item, "error_type": item["error_type"] or "-"},
            )
        )
    return "\n".join(lines) + "\n"


async def async_main() -> int:
    args = parse_args()
    load_environment()
    dataset = EvaluationDataset.from_yaml(args.dataset_path)
    if args.case_ids:
        selected = set(args.case_ids)
        dataset = EvaluationDataset(
            name=f"{dataset.name} (selected)",
            description=dataset.description,
            test_cases=[case for case in dataset if case.id in selected],
        )
        missing = sorted(selected - {case.id for case in dataset})
        if missing:
            raise ValueError(f"Unknown case ids: {missing}")

    database_id, dialect, sql_runner = build_sql_runner_from_env()
    runtime = SimpleNamespace(
        database_id=database_id,
        dialect=dialect,
        sql_runner=sql_runner,
        default_user=User(
            id="evaluation",
            username="evaluation",
            email="evaluation@example.com",
            group_memberships=["admin", "user"],
        ),
    )
    report = await validate_reference_queries(dataset, runtime)
    markdown = to_markdown(report)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.output_markdown:
        output = Path(args.output_markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0 if report["failure_count"] == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
