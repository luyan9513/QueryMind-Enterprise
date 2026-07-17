from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.bootstrap import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_RESUME_ROOT,
    resolve_agent_temperature,
    resolve_evaluation_providers,
)
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
