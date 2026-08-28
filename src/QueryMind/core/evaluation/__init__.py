"""QueryMind evaluation framework."""

from .base import (
    AgentResult,
    EvaluationResult,
    Evaluator,
    ExpectedOutcome,
    ExpectedSchema,
    ExpectedSqlContract,
    JudgeInput,
    JudgeResult,
    ResultComparisonPolicy,
    SqlExecutionArtifact,
    SqlTestCase,
    ToolInvocationRecord,
)
from .benchmark_admission import (
    BenchmarkAdmissionAssessment,
    BenchmarkAdmissionProfile,
    BenchmarkCaseStability,
    BenchmarkCoverageDeficit,
    BenchmarkQualityAssessment,
    BenchmarkQualityGate,
    BenchmarkQualityThresholds,
    BenchmarkSplitQuality,
    assess_benchmark_admission,
    assess_benchmark_quality,
    load_benchmark_admission_profile,
)
from .dataset import EvaluationDataset
from .evaluators import SqlAccuracyEvaluator
from .mode import EvaluationMode, parse_evaluation_mode
from .outcome import ExpectedOutcomeEvaluator
from .report import ComparisonReport, EvaluationReport
from .runner import EvaluationRunner
from .runtime import (
    DictEvaluationRuntimeResolver,
    EvaluationConversationStore,
    EvaluationRuntime,
    EvaluationRuntimeResolver,
    EvaluationSession,
    StaticUserResolver,
)
from .validation import DatasetValidationError, EvaluationDatasetValidator, ValidationIssue

__all__ = [
    "AgentResult",
    "BenchmarkAdmissionAssessment",
    "BenchmarkAdmissionProfile",
    "BenchmarkCoverageDeficit",
    "BenchmarkCaseStability",
    "BenchmarkSplitQuality",
    "BenchmarkQualityThresholds",
    "BenchmarkQualityAssessment",
    "BenchmarkQualityGate",
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
    "assess_benchmark_quality",
    "load_benchmark_admission_profile",
]
