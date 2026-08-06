"""QueryMind evaluation framework."""

from .base import (
    AgentResult,
    EvaluationResult,
    Evaluator,
    ExpectedOutcome,
    ExpectedSchema,
    ExpectedSqlContract,
    ResultComparisonPolicy,
    JudgeInput,
    JudgeResult,
    SqlExecutionArtifact,
    SqlTestCase,
    ToolInvocationRecord,
)
from .dataset import EvaluationDataset
from .benchmark_admission import (
    BenchmarkAdmissionAssessment,
    BenchmarkAdmissionProfile,
    BenchmarkCoverageDeficit,
    BenchmarkQualityThresholds,
    assess_benchmark_admission,
    load_benchmark_admission_profile,
)
from .evaluators import SqlAccuracyEvaluator
from .mode import EvaluationMode, parse_evaluation_mode
from .outcome import ExpectedOutcomeEvaluator
from .report import ComparisonReport, EvaluationReport
from .runner import EvaluationRunner
from .validation import DatasetValidationError, EvaluationDatasetValidator, ValidationIssue
from .runtime import (
    DictEvaluationRuntimeResolver,
    EvaluationConversationStore,
    EvaluationRuntime,
    EvaluationRuntimeResolver,
    EvaluationSession,
    StaticUserResolver,
)

__all__ = [
    "AgentResult",
    "BenchmarkAdmissionAssessment",
    "BenchmarkAdmissionProfile",
    "BenchmarkCoverageDeficit",
    "BenchmarkQualityThresholds",
    "EvaluationDataset",
    "EvaluationResult",
    "EvaluationMode",
    "EvaluationReport",
    "ComparisonReport",
    "EvaluationRunner",
    "EvaluationRuntime",
    "EvaluationRuntimeResolver",
    "EvaluationSession",
    "DictEvaluationRuntimeResolver",
    "EvaluationConversationStore",
    "StaticUserResolver",
    "Evaluator",
    "ExpectedOutcome",
    "ExpectedSchema",
    "ExpectedSqlContract",
    "ResultComparisonPolicy",
    "DatasetValidationError",
    "EvaluationDatasetValidator",
    "JudgeInput",
    "JudgeResult",
    "ExpectedOutcomeEvaluator",
    "SqlAccuracyEvaluator",
    "SqlExecutionArtifact",
    "SqlTestCase",
    "ValidationIssue",
    "ToolInvocationRecord",
    "parse_evaluation_mode",
    "assess_benchmark_admission",
    "load_benchmark_admission_profile",
]
