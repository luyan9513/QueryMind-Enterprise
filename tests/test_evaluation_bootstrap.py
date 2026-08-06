from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.bootstrap import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_RESUME_ROOT,
    build_sql_runner_from_env,
    resolve_agent_temperature,
    resolve_evaluation_mode,
    resolve_evaluation_providers,
    resolve_postgres_schemas,
)
from QueryMind.core.evaluation import EvaluationMode  # noqa: E402
import evals.bootstrap as bootstrap  # noqa: E402


def test_resolve_evaluation_providers_uses_role_specific_env(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_PROVIDER", raising=False)
    monkeypatch.setenv("EVAL_AGENT_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("EVAL_JUDGE_LLM_PROVIDER", "deepseek")

    agent_provider, judge_provider = resolve_evaluation_providers()

    assert agent_provider == "minimax"
    assert judge_provider == "deepseek"


def test_resolve_evaluation_providers_cli_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_LLM_PROVIDER", "minimax")
    monkeypatch.setenv("EVAL_JUDGE_LLM_PROVIDER", "deepseek")

    agent_provider, judge_provider = resolve_evaluation_providers("deepseek")

    assert agent_provider == "deepseek"
    assert judge_provider == "deepseek"


def test_bootstrap_defaults_live_under_eval_output() -> None:
    assert DEFAULT_OUTPUT_ROOT.name == "eval_output"
    assert DEFAULT_RESUME_ROOT.parent == DEFAULT_OUTPUT_ROOT
    assert DEFAULT_RESULTS_ROOT == DEFAULT_OUTPUT_ROOT / "eval_results"


def test_agent_temperature_defaults_to_deterministic_and_is_validated(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_AGENT_TEMPERATURE", raising=False)
    assert resolve_agent_temperature() == 0.0

    monkeypatch.setenv("EVAL_AGENT_TEMPERATURE", "0.2")
    assert resolve_agent_temperature() == 0.2

    monkeypatch.setenv("EVAL_AGENT_TEMPERATURE", "2.1")
    try:
        resolve_agent_temperature()
    except ValueError as exc:
        assert "between 0.0 and 2.0" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("out-of-range temperature should fail")


def test_evaluation_mode_prefers_explicit_value_then_env(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_MODE", "s1")
    assert resolve_evaluation_mode() == EvaluationMode.S1_AGENT_WITHOUT_PLAN
    assert resolve_evaluation_mode("s0") == EvaluationMode.S0_SINGLE_SHOT


def test_postgres_schema_scope_is_configurable_per_data_source(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_POSTGRES_SCHEMAS", raising=False)
    assert resolve_postgres_schemas() == [
        "person",
        "humanresources",
        "production",
        "purchasing",
        "sales",
    ]

    monkeypatch.setenv("EVAL_POSTGRES_SCHEMAS", " public, analytics,public ")
    assert resolve_postgres_schemas() == ["public", "analytics"]

    monkeypatch.setenv("EVAL_POSTGRES_SCHEMAS", " , ")
    try:
        resolve_postgres_schemas()
    except ValueError as exc:
        assert "at least one schema" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("empty schema scope should fail")


def test_load_environment_preserves_explicit_run_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "EVAL_SCHEMA_SYNC_MODE=sync\nEVAL_DATASET_PATH=from-dotenv.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("EVAL_SCHEMA_SYNC_MODE", "reuse_existing")
    monkeypatch.delenv("EVAL_DATASET_PATH", raising=False)

    bootstrap.load_environment()

    assert bootstrap.os.getenv("EVAL_SCHEMA_SYNC_MODE") == "reuse_existing"
    assert bootstrap.os.getenv("EVAL_DATASET_PATH") == "from-dotenv.yaml"


def test_build_sql_runner_does_not_require_model_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVAL_DATABASE_ID", "sqlite-demo")
    monkeypatch.setenv("EVAL_DB_DIALECT", "sqlite")
    monkeypatch.setenv("EVAL_SQLITE_DATABASE_PATH", str(tmp_path / "demo.sqlite"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    database_id, dialect, runner = build_sql_runner_from_env()

    assert database_id == "sqlite-demo"
    assert dialect == "sqlite"
    assert runner.__class__.__name__ == "SqliteRunner"
