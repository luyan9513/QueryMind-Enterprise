from QueryMind.core.agent.failure_recovery import (
    FailureCategory,
    FailureRecoveryState,
    RecoveryAction,
    build_failure_recovery_prompt,
    classify_tool_failure,
)
from QueryMind.core.tool import ToolCall, ToolResult


def _call(sql: str = "SELECT * FROM missing_table") -> ToolCall:
    return ToolCall(id="call-1", name="run_sql", arguments={"sql": sql})


def _failure(error: str, *, stage: str = "", code: str = "") -> ToolResult:
    metadata = {}
    if stage:
        metadata["rejection_stage"] = stage
    if code:
        metadata["rejection_code"] = code
    return ToolResult(
        success=False,
        result_for_llm=error,
        error=error,
        metadata=metadata,
    )


def test_failure_analyzer_routes_schema_and_security_failures() -> None:
    schema = classify_tool_failure(
        _call(),
        _failure('relation "missing_table" does not exist'),
    )
    assert schema is not None
    assert schema.category == FailureCategory.SCHEMA_EVIDENCE
    assert schema.action == RecoveryAction.RETRIEVE_SCHEMA
    assert schema.allowed_tools == ["schema_retrieve", "submit_query_plan"]

    security = classify_tool_failure(
        _call("DROP TABLE users"),
        _failure("blocked", stage="injection", code="sql_injection"),
    )
    assert security is not None
    assert security.action == RecoveryAction.STOP
    assert security.allowed_tools == []

    metadata = classify_tool_failure(
        _call("SELECT * FROM information_schema.tables"),
        _failure(
            "plan required",
            stage="planning",
            code="adaptive_query_plan_required",
        ),
    )
    assert metadata is not None
    assert metadata.category == FailureCategory.SCHEMA_EVIDENCE
    assert metadata.action == RecoveryAction.RETRIEVE_SCHEMA
    assert metadata.allowed_tools == ["schema_retrieve"]

    metadata_security = classify_tool_failure(
        _call("SELECT * FROM information_schema.tables"),
        _failure("blocked", stage="injection", code="sql_injection"),
    )
    assert metadata_security is not None
    assert metadata_security.category == FailureCategory.TERMINAL_SECURITY
    assert metadata_security.action == RecoveryAction.STOP


def test_metadata_query_variants_share_one_failure_fingerprint() -> None:
    state = FailureRecoveryState(max_same_failure_retries=2)
    result = _failure(
        "plan required",
        stage="planning",
        code="adaptive_query_plan_required",
    )
    first = state.observe(
        _call("SELECT table_name FROM information_schema.tables"),
        result,
    )
    second = state.observe(
        _call("SELECT column_name FROM information_schema.columns"),
        result,
    )
    assert first is not None and second is not None
    assert first.fingerprint == second.fingerprint
    assert second.exhausted is True


def test_failure_state_stops_repeated_identical_failure() -> None:
    state = FailureRecoveryState(max_same_failure_retries=2)
    result = _failure("syntax error near FROM")

    first = state.observe(_call("SELECT FROM orders"), result)
    second = state.observe(_call("SELECT FROM orders"), result)

    assert first is not None and first.action == RecoveryAction.REPAIR_SQL
    assert first.exhausted is False
    assert second is not None and second.action == RecoveryAction.STOP
    assert second.exhausted is True
    assert "Do not guess" in build_failure_recovery_prompt(second)


def test_success_clears_pending_recovery() -> None:
    state = FailureRecoveryState(max_same_failure_retries=2)
    state.observe(_call(), _failure("syntax error"))
    assert state.pending is not None

    state.observe(
        _call("SELECT 1"),
        ToolResult(success=True, result_for_llm="ok"),
    )
    assert state.pending is None
