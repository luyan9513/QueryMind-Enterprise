from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "evals"))

from QueryMind.core.evaluation import EvaluationDataset, SqlTestCase  # noqa: E402
from QueryMind.core.user import User  # noqa: E402
from validate_reference_sql import (  # noqa: E402
    to_markdown,
    validate_reference_queries,
)


class _FakeRunner:
    async def run_sql(self, args, context):
        if "broken" in args.sql:
            raise RuntimeError("connection details must not enter the report")
        if "empty" in args.sql:
            return pd.DataFrame(columns=["value"])
        return pd.DataFrame({"value": [1, 2]})


def _runtime(database_id: str = "demo") -> SimpleNamespace:
    return SimpleNamespace(
        database_id=database_id,
        dialect="postgres",
        sql_runner=_FakeRunner(),
        default_user=User(
            id="evaluation",
            username="evaluation",
            email="evaluation@example.com",
        ),
    )


@pytest.mark.asyncio
async def test_reference_validation_counts_success_empty_and_failure_safely() -> None:
    dataset = EvaluationDataset(
        name="demo",
        test_cases=[
            SqlTestCase(
                id="ok",
                database_id="demo",
                dialect="postgres",
                query="ok",
                ground_truth_sql="SELECT 1",
            ),
            SqlTestCase(
                id="empty",
                database_id="demo",
                dialect="postgres",
                query="empty",
                ground_truth_sql="SELECT 'empty'",
            ),
            SqlTestCase(
                id="broken",
                database_id="demo",
                dialect="postgres",
                query="broken",
                ground_truth_sql="SELECT 'broken'",
            ),
        ],
    )

    report = await validate_reference_queries(dataset, _runtime())

    assert report["success_count"] == 2
    assert report["failure_count"] == 1
    assert report["empty_result_count"] == 1
    assert report["results"][2]["error_type"] == "RuntimeError"
    assert "connection details" not in to_markdown(report)


@pytest.mark.asyncio
async def test_reference_validation_rejects_wrong_database() -> None:
    dataset = EvaluationDataset(
        name="demo",
        test_cases=[
            SqlTestCase(
                id="case-1",
                database_id="other",
                dialect="postgres",
                query="ok",
                ground_truth_sql="SELECT 1",
            )
        ],
    )

    with pytest.raises(ValueError, match="do not match runtime"):
        await validate_reference_queries(dataset, _runtime())
