"""Evaluation reporting for QueryMind."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .base import EvaluationResult
from .metrics import estimate_usage_cost_usd, merge_token_usage, percentile
from .sanitization import redact_sensitive_text, sanitize_export_payload


_FAILURE_EXPLANATIONS = {
    "success": "结果和题目约束均通过，没有发现确定性错误。",
    "dataset_failure": "参考 SQL 或评测数据本身不可执行，需要先修复评测集。",
    "provider_failure": "模型服务发生超时、限流或连接异常，结果不能代表 SQL 能力。",
    "permission_failure": "查询被用户权限或行级权限规则拦截。",
    "sql_generation_failure": "Agent 没有产出可供评测的 SQL。",
    "query_contract_failure": "SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。",
    "sql_execution_failure": "SQL 语法、字段、表或数据库执行过程出错。",
    "result_mismatch": "SQL 可以执行，但完整结果与参考结果不一致。",
    "schema_recall_failure": "生成第一条 SQL 前没有召回完整的必要表，模型可能缺少连接路径或字段依据。",
    "governance_rejection": "SQL 被只读、安全或语义治理规则拦截；若后续修复成功，它只算过程信号。",
    "answer_format_failure": "SQL 结果可能正确，但最终回答缺少约定内容或格式不符合要求。",
    "unknown": "现有证据不足以归到已知类别，需要结合工具轨迹人工复核。",
}


def _plain_contract_violation(value: str) -> str:
    kind, _, detail = str(value).partition(":")
    labels = {
        "sql_parse_error": "SQL 无法解析",
        "missing_feature": f"缺少必要 SQL 结构：{detail}",
        "forbidden_feature": f"出现不允许的 SQL 结构：{detail}",
        "missing_column": f"缺少必要字段：{detail}",
        "forbidden_column": f"使用了不允许的字段：{detail}",
        "missing_filter_column": f"缺少必要过滤字段：{detail}",
        "missing_projection_alias": f"缺少必要输出列：{detail}",
        "projection_count_below_min": f"输出列过少：{detail}",
        "projection_count_above_max": f"输出列过多：{detail}",
    }
    return labels.get(kind, str(value))


def _case_problem_summary(result: EvaluationResult) -> List[str]:
    metadata = result.metadata or {}
    problems: List[str] = []
    for violation in metadata.get("sql_contract_violations", []):
        problems.append(_plain_contract_violation(str(violation)))
    if result.agent_artifact is not None and not result.agent_artifact.success:
        error = result.agent_artifact.error_message or "数据库未返回成功状态"
        problems.append(f"SQL 执行失败：{redact_sensitive_text(str(error))}")
    strict_correct = metadata.get(
        "verified_result_correct",
        metadata.get("result_correct"),
    )
    business_correct = metadata.get("business_result_correct")
    if strict_correct is False and business_correct is True:
        problems.append("原始结果表示不同，但按题目声明的精度、别名或顺序规则归一化后业务等价")
    elif strict_correct is False and result.agent_artifact is not None:
        problems.append("完整结果与参考 SQL 不一致")
    missing_tables = metadata.get("missing_tables") or []
    if missing_tables:
        problems.append("首条 SQL 前未召回必要表：" + ", ".join(map(str, missing_tables)))
    if not problems:
        problems.append("未发现确定性错误")
    return list(dict.fromkeys(problems))


def _case_improvement_suggestion(result: EvaluationResult) -> str:
    failure = str(result.metadata.get("primary_failure") or "unknown")
    suggestions = {
        "success": "保留为回归样例，防止后续优化造成退化。",
        "schema_recall_failure": "改写 Schema 检索词，补充 required_fields，并从高相关表沿外键做一跳扩展。",
        "query_contract_failure": "重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。",
        "result_mismatch": "对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。",
        "sql_execution_failure": "先根据数据库错误修正表名、字段名、函数或方言，再复核业务语义。",
        "sql_generation_failure": "减少无效工具循环；证据不足时应向用户澄清，而不是猜测 SQL。",
        "permission_failure": "核对用户组、RLS 范围和允许访问的业务域，不应绕过权限。",
        "provider_failure": "单独重试该样例并记录服务状态，不把云服务异常混入准确率结论。",
        "governance_rejection": "按照治理层返回的原因做最小修复，避免重复提交同类危险或元数据 SQL。",
        "answer_format_failure": "保持已验证 SQL 不变，只修正最终回答的字段说明和表达格式。",
        "dataset_failure": "修复参考 SQL 或测试数据后重新执行，修复前不用于模型对比。",
        "unknown": "查看工具轨迹和两条 SQL 的差异，补充新的失败分类或题目契约。",
    }
    return suggestions.get(failure, suggestions["unknown"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvaluationReport(BaseModel):
    """Report for one evaluation run."""

    dataset_name: str
    results: List[EvaluationResult]
    evaluator_names: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utc_now)

    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for item in self.results if item.passed) / len(self.results)

    def average_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(item.score for item in self.results) / len(self.results)

    def average_agent_execution_time(self) -> float:
        """Average runtime of the evaluated agent only."""
        if not self.results:
            return 0.0
        return sum(item.agent_result.execution_time_ms for item in self.results) / len(
            self.results
        )

    def average_execution_time(self) -> float:
        return self.average_agent_execution_time()

    def execution_success_rate(self) -> float:
        if not self.results:
            return 0.0
        successful = sum(
            1
            for item in self.results
            if item.agent_artifact is not None and item.agent_artifact.success
        )
        return successful / len(self.results)

    def result_correct_rate(self) -> float:
        measured = [
            item
            for item in self.results
            if "verified_result_correct" in (item.metadata or {})
            or "result_correct" in (item.metadata or {})
        ]
        if not measured:
            return 0.0
        return sum(
            bool(
                item.metadata.get(
                    "verified_result_correct",
                    item.metadata.get("result_correct"),
                )
            )
            for item in measured
        ) / len(measured)

    def business_result_correct_rate(self) -> float:
        measured = [
            item
            for item in self.results
            if "business_result_correct" in (item.metadata or {})
        ]
        if not measured:
            return 0.0
        return sum(
            bool(item.metadata.get("business_result_correct")) for item in measured
        ) / len(measured)

    def sql_contract_pass_rate(self) -> float:
        measured = [
            item
            for item in self.results
            if "sql_contract_passed" in (item.metadata or {})
        ]
        if not measured:
            return 0.0
        return sum(
            bool(item.metadata.get("sql_contract_passed")) for item in measured
        ) / len(measured)

    def first_sql_execution_success_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(
            bool(item.metadata.get("first_sql_execution_success"))
            for item in self.results
        ) / len(self.results)

    def first_sql_result_correct_rate(self) -> float:
        measured = [
            item
            for item in self.results
            if "first_sql_result_correct" in (item.metadata or {})
        ]
        if not measured:
            return 0.0
        return sum(
            bool(item.metadata.get("first_sql_result_correct")) for item in measured
        ) / len(measured)

    def average_schema_recall(self) -> float:
        values = [
            float(item.metadata["schema_recall"])
            for item in self.results
            if item.metadata.get("schema_recall") is not None
        ]
        return sum(values) / len(values) if values else 0.0

    def average_tool_calls(self) -> float:
        if not self.results:
            return 0.0
        return sum(
            int(item.metadata.get("tool_call_count", len(item.agent_result.tool_calls)))
            for item in self.results
        ) / len(self.results)

    def p95_tool_calls(self) -> float:
        return percentile(
            [
                int(item.metadata.get("tool_call_count", len(item.agent_result.tool_calls)))
                for item in self.results
            ],
            0.95,
        )

    def p95_agent_execution_time(self) -> float:
        return percentile(
            [item.agent_result.execution_time_ms for item in self.results],
            0.95,
        )

    def failure_distribution(self) -> Dict[str, int]:
        return dict(
            Counter(
                str(item.metadata.get("primary_failure") or "unknown")
                for item in self.results
            )
        )

    def secondary_failure_distribution(self) -> Dict[str, int]:
        counter: Counter[str] = Counter()
        for item in self.results:
            counter.update(
                str(value)
                for value in item.metadata.get("secondary_failures", [])
                if value
            )
        return dict(counter)

    def token_usage(self, role: str) -> Dict[str, int]:
        if role == "agent":
            return merge_token_usage(item.agent_result.token_usage for item in self.results)
        if role == "judge":
            return merge_token_usage(
                item.judge_result.token_usage
                for item in self.results
                if item.judge_result is not None
            )
        return {}

    def estimated_cost_usd(self, role: str) -> Optional[float]:
        pricing = self._config_snapshot().get("pricing", {})
        role_pricing = pricing.get(role) if isinstance(pricing, dict) else None
        return estimate_usage_cost_usd(self.token_usage(role), role_pricing)

    def total_estimated_cost_usd(self) -> Optional[float]:
        costs = [self.estimated_cost_usd("agent"), self.estimated_cost_usd("judge")]
        available = [cost for cost in costs if cost is not None]
        return sum(available) if available else None

    def issue_tag_distribution(self) -> Dict[str, int]:
        counter: Counter[str] = Counter()
        for result in self.results:
            counter.update(result.issue_tags)
        return dict(counter)

    def run_status(self) -> str:
        return str(self.metadata.get("run_status") or "completed")

    def run_progress(self) -> Optional[str]:
        completed = self.metadata.get("completed_test_cases")
        total = self.metadata.get("total_test_cases")
        if completed is None or total is None:
            return None
        return f"{completed}/{total}"

    def classification_summaries(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            field: self._build_classification_summary(field)
            for field in self.classification_fields()
        }

    def evaluator_summaries(self) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[EvaluationResult]] = defaultdict(list)
        for result in self.results:
            for evaluator_name, evaluator_result in self._per_evaluator_results(result).items():
                grouped[evaluator_name].append(evaluator_result)

        ordered_names = list(self.evaluator_names)
        for name in grouped:
            if name not in ordered_names:
                ordered_names.append(name)

        summaries: List[Dict[str, Any]] = []
        for name in ordered_names:
            items = grouped.get(name, [])
            if not items:
                continue
            summaries.append(
                {
                    "evaluator_name": name,
                    "count": len(items),
                    "pass_count": sum(1 for item in items if item.passed),
                    "pass_rate": sum(1 for item in items if item.passed) / len(items),
                    "average_score": sum(item.score for item in items) / len(items),
                    "average_execution_time_ms": sum(
                        item.agent_result.execution_time_ms for item in items
                    )
                    / len(items),
                    "issue_tags": dict(
                        Counter(tag for item in items for tag in item.issue_tags)
                    ),
                }
            )
        return summaries

    def classification_fields(self) -> List[str]:
        return [
            "difficulty",
            "category",
            "source",
            "query_language",
            "business_domain",
        ]

    def enrich_metadata(self) -> None:
        self.metadata["classification_summaries"] = self.classification_summaries()
        self.metadata["evaluator_summaries"] = self.evaluator_summaries()
        self.metadata["classification_fields"] = self.classification_fields()
        self.metadata["enterprise_metrics"] = {
            "schema_recall": self.average_schema_recall(),
            "sql_execution_success_rate": self.execution_success_rate(),
            "result_correct_rate": self.result_correct_rate(),
            "business_result_correct_rate": self.business_result_correct_rate(),
            "sql_contract_pass_rate": self.sql_contract_pass_rate(),
            "first_sql_execution_success_rate": self.first_sql_execution_success_rate(),
            "first_sql_result_correct_rate": self.first_sql_result_correct_rate(),
            "average_tool_calls": self.average_tool_calls(),
            "p95_tool_calls": self.p95_tool_calls(),
            "p95_agent_execution_time_ms": self.p95_agent_execution_time(),
            "failure_distribution": self.failure_distribution(),
            "secondary_failure_distribution": self.secondary_failure_distribution(),
            "agent_token_usage": self.token_usage("agent"),
            "judge_token_usage": self.token_usage("judge"),
            "estimated_cost_usd": self.total_estimated_cost_usd(),
        }

    def get_failures(self) -> List[EvaluationResult]:
        return [item for item in self.results if not item.passed]

    def _config_snapshot(self) -> Dict[str, Any]:
        snapshot = self.metadata.get("config_snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    def _metadata_value(self, key: str) -> Any:
        snapshot = self._config_snapshot()
        if key in snapshot and snapshot[key] not in (None, ""):
            return snapshot[key]

        value = self.metadata.get(key)
        if value not in (None, ""):
            return value
        return None

    def _evaluation_model_info(self) -> List[tuple[str, str]]:
        return [
            ("Agent Model", str(self._metadata_value("agent_model") or "n/a")),
            ("Judge Model", str(self._metadata_value("judge_model") or "n/a")),
        ]

    def _sql_execution_status(self, result: EvaluationResult) -> str:
        artifact = result.agent_artifact
        return "SUCCESS" if artifact is not None and artifact.success else "FAIL"

    def print_summary(self) -> None:
        self.enrich_metadata()
        print(f"\n{'=' * 80}")
        print(f"EVALUATION REPORT: {self.dataset_name}")
        print(f"{'=' * 80}")
        print(f"Timestamp: {self.timestamp.isoformat()}")
        print(f"Run Status: {self.run_status()}")
        progress = self.run_progress()
        if progress:
            print(f"Progress: {progress}")
        print(f"Test Cases: {len(self.results)}")
        if self.evaluator_names:
            print(f"Evaluators: {', '.join(self.evaluator_names)}")
        print(f"Evaluator Pass Rate: {self.pass_rate():.2%}")
        print(f"Average Score: {self.average_score():.2f}")
        print(f"Execution Success Rate: {self.execution_success_rate():.2%}")
        print(f"Strict Result Correct Rate: {self.result_correct_rate():.2%}")
        print(f"Business Result Correct Rate: {self.business_result_correct_rate():.2%}")
        print(f"SQL Contract Pass Rate: {self.sql_contract_pass_rate():.2%}")
        print(f"Schema Recall: {self.average_schema_recall():.2%}")
        print(
            f"First SQL Result Correct Rate: {self.first_sql_result_correct_rate():.2%}"
        )
        print(f"Average Eval Agent Time: {self.average_execution_time():.0f}ms")
        print(f"{'=' * 80}\n")

    def save_json(self, path: str | Path) -> None:
        self.enrich_metadata()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                sanitize_export_payload(self.model_dump(mode="json")),
                f,
                indent=2,
                ensure_ascii=False,
            )

    def save_csv(self, path: str | Path) -> None:
        self.enrich_metadata()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "test_case_id",
                    "database_id",
                    "dialect",
                    "difficulty",
                    "category",
                    "source",
                    "query_language",
                    "business_domain",
                    "passed",
                    "score",
                    "reason",
                    "issue_tags",
                    "execution_time_ms",
                    "schema_recall",
                    "result_correct",
                    "business_result_correct",
                    "sql_contract_passed",
                    "sql_contract_violations",
                    "first_sql_execution_success",
                    "first_sql_result_correct",
                    "tool_call_count",
                    "primary_failure",
                    "secondary_failures",
                    "evaluator_breakdown",
                    "agent_sql",
                    "ground_truth_sql",
                ]
            )
            for result in self.results:
                classifications = result.test_case.classification_dimensions()
                writer.writerow(
                    [
                        result.test_case.id,
                        result.test_case.database_id,
                        result.test_case.dialect,
                        classifications["difficulty"],
                        classifications["category"],
                        classifications["source"],
                        classifications["query_language"],
                        classifications["business_domain"],
                        result.passed,
                        f"{result.score:.4f}",
                        result.reason,
                        ",".join(result.issue_tags),
                        f"{result.agent_result.execution_time_ms:.2f}",
                        result.metadata.get("schema_recall", ""),
                        result.metadata.get("result_correct", ""),
                        result.metadata.get("business_result_correct", ""),
                        result.metadata.get("sql_contract_passed", ""),
                        ",".join(result.metadata.get("sql_contract_violations", [])),
                        result.metadata.get("first_sql_execution_success", ""),
                        result.metadata.get("first_sql_result_correct", ""),
                        result.metadata.get("tool_call_count", ""),
                        result.metadata.get("primary_failure", "unknown"),
                        ",".join(result.metadata.get("secondary_failures", [])),
                        self._format_evaluator_breakdown(result),
                        result.agent_artifact.sql_text if result.agent_artifact else "",
                        result.ground_truth_artifact.sql_text if result.ground_truth_artifact else "",
                    ]
                )

    def save_markdown(self, path: str | Path) -> None:
        self.enrich_metadata()
        lines = [
            f"# Evaluation Report: {self.dataset_name}",
            "",
            f"- Timestamp: {self.timestamp.isoformat()}",
            f"- Run status: {self.run_status()}",
            f"- Test cases: {len(self.results)}",
            f"- Evaluators: {', '.join(self.evaluator_names) if self.evaluator_names else 'n/a'}",
            f"- Evaluator pass rate: {self.pass_rate():.2%}",
            f"- Average score: {self.average_score():.2f}",
            f"- Schema recall: {self.average_schema_recall():.2%}",
            f"- SQL execution success rate: {self.execution_success_rate():.2%}",
            f"- Strict result correct rate: {self.result_correct_rate():.2%}",
            f"- Business result correct rate: {self.business_result_correct_rate():.2%}",
            f"- SQL contract pass rate: {self.sql_contract_pass_rate():.2%}",
            f"- First SQL execution success rate: {self.first_sql_execution_success_rate():.2%}",
            f"- First SQL result correct rate: {self.first_sql_result_correct_rate():.2%}",
            f"- Average / P95 tool calls: {self.average_tool_calls():.2f} / {self.p95_tool_calls():.2f}",
            f"- P95 Eval Agent Time: {self.p95_agent_execution_time():.0f}ms",
            f"- Average Eval Agent Time: {self.average_execution_time():.0f}ms",
            "",
            "## Evaluator Summary",
            "| Evaluator | Cases | Pass Rate | Avg Score | Avg Eval Agent Time (ms) |",
            "|---|---:|---:|---:|---:|",
        ]
        for summary in self.evaluator_summaries():
            lines.append(
                f"| {summary['evaluator_name']} | {summary['count']} | "
                f"{summary['pass_rate']:.2%} | {summary['average_score']:.2f} | "
                f"{summary['average_execution_time_ms']:.0f} |"
            )

        for field, rows in self.classification_summaries().items():
            lines.extend(
                [
                    "",
                    f"## {self._classification_label(field)} Breakdown",
                    "| Value | Cases | Evaluator Pass Rate | Avg Score | Avg Eval Agent Time (ms) |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for row in rows:
                lines.append(
                    f"| {row['value']} | {row['count']} | {row['pass_rate']:.2%} | "
                    f"{row['average_score']:.2f} | {row['average_execution_time_ms']:.0f} |"
                )

        lines.extend(
            [
                "",
                "## Strict Primary Failure Attribution",
                "| Primary failure | Cases |",
                "|---|---:|",
            ]
        )
        for failure_name, count in sorted(
            self.failure_distribution().items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"| {failure_name} | {count} |")

        lines.extend(
            [
                "",
                "## Recovered / Secondary Signals",
                "| Signal | Cases |",
                "|---|---:|",
            ]
        )
        for signal_name, count in sorted(
            self.secondary_failure_distribution().items(),
            key=lambda item: (-item[1], item[0]),
        ):
            lines.append(f"| {signal_name} | {count} |")

        lines.extend(
            [
                "",
                "| Test Case | DB | Difficulty | Category | Schema Recall | Strict Result Correct | First SQL Strict Correct | Failure | Evaluator Pass | Score | Reason |",
                "|---|---|---|---|---:|---:|---:|---|---:|---:|---|",
            ]
        )
        for result in self.results:
            reason = result.reason.replace("|", "\\|")
            classifications = result.test_case.classification_dimensions()
            lines.append(
                f"| {result.test_case.id} | {result.test_case.database_id} | "
                f"{classifications['difficulty']} | {classifications['category']} | "
                f"{result.metadata.get('schema_recall', 'n/a')} | "
                f"{result.metadata.get('result_correct', 'n/a')} | "
                f"{result.metadata.get('first_sql_result_correct', 'n/a')} | "
                f"{result.metadata.get('primary_failure', 'unknown')} | "
                f"{result.passed} | {result.score:.2f} | {reason} |"
            )
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def save_detailed_markdown(self, path: str | Path) -> None:
        """Save a human-readable, per-case SQL accuracy audit in Chinese."""
        self.enrich_metadata()
        total_cost = self.total_estimated_cost_usd()
        cost_label = f"${total_cost:.6f}" if total_cost is not None else "未配置价格"
        lines = [
            f"# 系统评测明细：{self.dataset_name}",
            "",
            "> 这份文档回答四个问题：系统准确率怎样、每题生成了什么 SQL、哪里错了、为什么错。",
            "> 评测题里的参考 SQL 和 SQL 契约只用于离线评分，不会注入运行时 Agent。",
            "",
            "## 一、总体结论",
            "",
            f"- 运行时间：{self.timestamp.isoformat()}",
            f"- 运行状态：{self.run_status()}",
            f"- 样例数：{len(self.results)}",
            f"- 严格结果准确率：{self.result_correct_rate():.2%}",
            f"- 业务等价准确率：{self.business_result_correct_rate():.2%}",
            f"- SQL 契约通过率：{self.sql_contract_pass_rate():.2%}",
            f"- SQL 可执行率：{self.execution_success_rate():.2%}",
            f"- 首条 SQL 严格正确率：{self.first_sql_result_correct_rate():.2%}",
            f"- Schema Recall：{self.average_schema_recall():.2%}",
            f"- 平均 / P95 工具调用：{self.average_tool_calls():.2f} / {self.p95_tool_calls():.2f}",
            f"- 平均 / P95 Agent 延迟：{self.average_execution_time() / 1000:.2f}s / {self.p95_agent_execution_time() / 1000:.2f}s",
            f"- 估算模型成本：{cost_label}",
            "",
            "### 指标怎么理解",
            "",
            "- 严格结果准确率：完整结果值、行数、列数和题目契约同时通过，最保守。",
            "- 业务等价准确率：允许评测题显式声明的小数精度、同义值和顺序差异，但仍必须通过 SQL 契约。",
            "- SQL 契约：检查必要聚合、分组、过滤字段、输出列等结构，防止错误 SQL 被宽松 Judge 误判为正确。",
            "- Evaluator 通过率不是严格准确率；其中可能包含大模型 Judge 的判断，应结合上面三个确定性指标阅读。",
            "- 任何指标都只代表当前数据、当前问题集和当前模型配置，不能证明换一套数据后仍然 100% 正确。",
            "",
            "## 二、失败分布",
            "",
            "| 失败类型 | 数量 | 大白话解释 |",
            "|---|---:|---|",
        ]
        for failure, count in sorted(
            self.failure_distribution().items(),
            key=lambda item: (-item[1], item[0]),
        ):
            lines.append(
                f"| {failure} | {count} | {_FAILURE_EXPLANATIONS.get(failure, _FAILURE_EXPLANATIONS['unknown'])} |"
            )
        if not self.failure_distribution():
            lines.append("| n/a | 0 | 没有可统计结果 |")

        lines.extend(["", "## 三、逐题明细", ""])
        for index, result in enumerate(self.results, 1):
            metadata = result.metadata or {}
            strict_correct = metadata.get(
                "verified_result_correct",
                metadata.get("result_correct"),
            )
            business_correct = metadata.get("business_result_correct")
            contract_passed = metadata.get("sql_contract_passed")
            execution_success = bool(
                result.agent_artifact is not None and result.agent_artifact.success
            )
            failure = str(metadata.get("primary_failure") or "unknown")
            reference_sql = (
                result.ground_truth_artifact.sql_text
                if result.ground_truth_artifact is not None
                else result.test_case.ground_truth_sql
            )
            agent_sql = (
                result.agent_artifact.sql_text
                if result.agent_artifact is not None
                else result.agent_result.get_primary_sql() or "-- 未生成 SQL"
            )
            problems = _case_problem_summary(result)
            reason = redact_sensitive_text(result.reason or "无额外说明")
            schema_recall = metadata.get("schema_recall")
            schema_label = (
                f"{float(schema_recall):.2%}" if schema_recall is not None else "未测量"
            )
            lines.extend(
                [
                    f"### {index}. {result.test_case.id}",
                    "",
                    f"- 用户问题：{result.test_case.query}",
                    f"- 结论：严格正确={strict_correct}；业务等价={business_correct}；SQL 契约={contract_passed}；可执行={execution_success}",
                    f"- Schema Recall：{schema_label}",
                    f"- 工具调用 / Agent 延迟：{metadata.get('tool_call_count', len(result.agent_result.tool_calls))} 次 / {result.agent_result.execution_time_ms / 1000:.2f}s",
                    f"- 主要失败类型：{failure}",
                    "",
                    "参考 SQL：",
                    "",
                    "```sql",
                    redact_sensitive_text(reference_sql, max_length=20000),
                    "```",
                    "",
                    "Agent 最终 SQL：",
                    "",
                    "```sql",
                    redact_sensitive_text(agent_sql, max_length=20000),
                    "```",
                    "",
                    "哪里错了：",
                    "",
                ]
            )
            lines.extend(f"- {problem}" for problem in problems)
            lines.extend(
                [
                    "",
                    "为什么错：",
                    "",
                    f"- {_FAILURE_EXPLANATIONS.get(failure, _FAILURE_EXPLANATIONS['unknown'])}",
                    f"- 评测器说明：{reason}",
                    "",
                    "建议怎么改：",
                    "",
                    f"- {_case_improvement_suggestion(result)}",
                    "",
                ]
            )
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def save_html(self, path: str | Path) -> None:
        self.enrich_metadata()
        rows = []
        for result in self.results:
            classifications = result.test_case.classification_dimensions()
            pass_label = "PASS" if result.passed else "FAIL"
            sql_execution_status = self._sql_execution_status(result)
            primary_failure = str(result.metadata.get("primary_failure") or "unknown")
            schema_recall = result.metadata.get("schema_recall")
            schema_recall_label = (
                f"{float(schema_recall):.0%}" if schema_recall is not None else "N/A"
            )
            result_correct = result.metadata.get("result_correct")
            business_correct = result.metadata.get("business_result_correct")
            contract_passed = result.metadata.get("sql_contract_passed")
            first_correct = result.metadata.get("first_sql_result_correct")
            rows.append(
                "<tr"
                f' data-difficulty="{html.escape(classifications["difficulty"], quote=True)}"'
                f' data-category="{html.escape(classifications["category"], quote=True)}"'
                f' data-source="{html.escape(classifications["source"], quote=True)}"'
                f' data-query-language="{html.escape(classifications["query_language"], quote=True)}"'
                f' data-business-domain="{html.escape(classifications["business_domain"], quote=True)}"'
                f' data-passed="{pass_label}"'
                f' data-sql-execution="{sql_execution_status}"'
                f' data-primary-failure="{html.escape(primary_failure, quote=True)}"'
                ">"
                f"<td>{html.escape(result.test_case.id)}</td>"
                f"<td>{html.escape(result.test_case.database_id)}</td>"
                f"<td>{html.escape(classifications['difficulty'])}</td>"
                f"<td>{html.escape(classifications['category'])}</td>"
                f"<td>{html.escape(classifications['source'])}</td>"
                f"<td>{html.escape(classifications['query_language'])}</td>"
                f"<td>{html.escape(classifications['business_domain'])}</td>"
                f"<td>{pass_label}</td>"
                f"<td>{sql_execution_status}</td>"
                f"<td>{schema_recall_label}</td>"
                f"<td>{self._boolean_label(result_correct)}</td>"
                f"<td>{self._boolean_label(business_correct)}</td>"
                f"<td>{self._boolean_label(contract_passed)}</td>"
                f"<td>{self._boolean_label(first_correct)}</td>"
                f"<td>{int(result.metadata.get('tool_call_count', len(result.agent_result.tool_calls)))}</td>"
                f"<td>{html.escape(primary_failure)}</td>"
                f"<td>{result.score:.2f}</td>"
                f"<td>{html.escape(self._format_evaluator_breakdown(result))}</td>"
                f"<td>{html.escape(result.reason)}</td>"
                "</tr>"
            )

        classification_sections = []
        for field, rows_summary in self.classification_summaries().items():
            table_rows = []
            for row in rows_summary:
                table_rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(row['value']))}</td>"
                    f"<td>{row['count']}</td>"
                    f"<td>{row['pass_rate']:.2%}</td>"
                    f"<td>{row['average_score']:.2f}</td>"
                    f"<td>{row['average_execution_time_ms']:.0f}</td>"
                    "</tr>"
                )
            classification_sections.append(
                f"<section class='summary-block'><h3>{html.escape(self._classification_label(field))} Breakdown</h3>"
                "<table>"
                "<thead><tr><th>Value</th><th>Cases</th><th>Evaluator Pass Rate</th><th>Avg Score</th><th>Avg Eval Agent Time (ms)</th></tr></thead>"
                f"<tbody>{''.join(table_rows)}</tbody>"
                "</table></section>"
            )

        evaluator_rows = []
        for summary in self.evaluator_summaries():
            evaluator_rows.append(
                "<tr>"
                f"<td>{html.escape(summary['evaluator_name'])}</td>"
                f"<td>{summary['count']}</td>"
                f"<td>{summary['pass_rate']:.2%}</td>"
                f"<td>{summary['average_score']:.2f}</td>"
                f"<td>{summary['average_execution_time_ms']:.0f}</td>"
                "</tr>"
            )

        difficulty_options = self._classification_options("difficulty")
        category_options = self._classification_options("category")
        source_options = self._classification_options("source")
        language_options = self._classification_options("query_language")
        domain_options = self._classification_options("business_domain")
        failure_options = sorted(self.failure_distribution())
        failure_rows = "".join(
            "<tr>"
            f"<td>{html.escape(name)}</td><td>{count}</td>"
            f"<td>{count / len(self.results):.2%}</td>"
            "</tr>"
            for name, count in sorted(
                self.failure_distribution().items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        secondary_failure_rows = "".join(
            "<tr>"
            f"<td>{html.escape(name)}</td><td>{count}</td>"
            f"<td>{count / len(self.results):.2%}</td>"
            "</tr>"
            for name, count in sorted(
                self.secondary_failure_distribution().items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        total_cost = self.total_estimated_cost_usd()
        cost_label = f"${total_cost:.6f}" if total_cost is not None else "N/A"
        model_info_rows = "".join(
            f"<div class='model-row'><span>{html.escape(label)}</span>"
            f"<strong>{html.escape(value)}</strong></div>"
            for label, value in self._evaluation_model_info()
        )
        run_status = self.run_status()
        progress = self.run_progress()
        banner = ""
        if run_status and run_status != "completed":
            progress_text = f" ({progress})" if progress else ""
            banner = (
                f"<section class='banner warning'><strong>Partial run</strong>"
                f"<span> - status: {html.escape(run_status)}{html.escape(progress_text)}</span>"
                "</section>"
            )

        html_text = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>QueryMind Evaluation Report</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f1f1f; background: #fff; max-width: 100%; overflow-x: hidden; }}
section {{ margin-bottom: 24px; max-width: 100%; overflow-x: auto; }}
.banner {{ padding: 12px 16px; border-radius: 10px; margin-bottom: 16px; }}
.banner.warning {{ background: #fff8e1; border: 1px solid #f2c94c; color: #6b4f00; }}
.report-header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 8px; flex-wrap: wrap; }}
.report-title {{ min-width: 0; flex: 1 1 320px; }}
.model-card {{ flex: 0 1 360px; padding: 14px 16px; border: 1px solid #d7dde5; border-radius: 14px; background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%); box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06); }}
.model-card .card-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }}
.model-row {{ display: flex; justify-content: space-between; gap: 16px; padding: 8px 0; border-top: 1px solid #e8edf3; }}
.model-row:first-of-type {{ border-top: 0; padding-top: 0; }}
.model-row span {{ color: #475569; }}
.model-row strong {{ color: #0f172a; text-align: right; word-break: break-word; }}
.metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.metric {{ padding: 12px 16px; border: 1px solid #ddd; border-radius: 10px; background: #fafafa; }}
.metric .label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.04em; }}
.metric .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; overflow-wrap: anywhere; }}
.filters {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.filters label {{ display: block; font-size: 12px; color: #444; margin-bottom: 4px; }}
.filters select {{ width: 100%; padding: 8px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
td {{ overflow-wrap: anywhere; }}
th {{ background: #f5f5f5; }}
tbody tr:nth-child(even) {{ background: #fcfcfc; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }}
.summary-block table {{ margin-top: 8px; }}
.muted {{ color: #666; font-size: 12px; }}
@media (max-width: 600px) {{
  body {{ margin: 12px; }}
  .metric .value {{ font-size: 20px; }}
  .model-card {{ flex-basis: 100%; }}
  .summary-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="report-header">
  <div class="report-title">
    <h1>{html.escape(self.dataset_name)}</h1>
    <p class="muted">Timestamp: {self.timestamp.isoformat()}</p>
  </div>
  <aside class="model-card">
    <div class="card-label">Batch Models</div>
    {model_info_rows}
  </aside>
</div>
{banner}
<div class="metrics">
  <div class="metric"><div class="label">Test Cases</div><div class="value">{len(self.results)}</div></div>
  <div class="metric"><div class="label">Run Status</div><div class="value">{html.escape(run_status)}</div></div>
  <div class="metric"><div class="label">Evaluator Pass Rate</div><div class="value">{self.pass_rate():.2%}</div></div>
  <div class="metric"><div class="label">Average Score</div><div class="value">{self.average_score():.2f}</div></div>
  <div class="metric"><div class="label">Schema Recall</div><div class="value">{self.average_schema_recall():.2%}</div></div>
  <div class="metric"><div class="label">Execution Success Rate</div><div class="value">{self.execution_success_rate():.2%}</div></div>
  <div class="metric"><div class="label">Strict Result Correct Rate</div><div class="value">{self.result_correct_rate():.2%}</div></div>
  <div class="metric"><div class="label">Business Result Correct Rate</div><div class="value">{self.business_result_correct_rate():.2%}</div></div>
  <div class="metric"><div class="label">SQL Contract Pass Rate</div><div class="value">{self.sql_contract_pass_rate():.2%}</div></div>
  <div class="metric"><div class="label">First SQL Correct Rate</div><div class="value">{self.first_sql_result_correct_rate():.2%}</div></div>
  <div class="metric"><div class="label">Avg / P95 Tool Calls</div><div class="value">{self.average_tool_calls():.1f} / {self.p95_tool_calls():.1f}</div></div>
  <div class="metric"><div class="label">Average Eval Agent Time</div><div class="value">{self.average_execution_time():.0f}ms</div></div>
  <div class="metric"><div class="label">P95 Eval Agent Time</div><div class="value">{self.p95_agent_execution_time():.0f}ms</div></div>
  <div class="metric"><div class="label">Estimated Model Cost</div><div class="value">{cost_label}</div></div>
</div>
<section>
<h2>Evaluator Summary</h2>
<table>
<thead><tr><th>Evaluator</th><th>Cases</th><th>Pass Rate</th><th>Avg Score</th><th>Avg Eval Agent Time (ms)</th></tr></thead>
<tbody>
{''.join(evaluator_rows)}
</tbody>
</table>
</section>
<section class="summary-grid">
{''.join(classification_sections)}
</section>
<section>
<h2>Strict Primary Failure Attribution</h2>
<table>
<thead><tr><th>Primary Failure</th><th>Cases</th><th>Share</th></tr></thead>
<tbody>{failure_rows}</tbody>
</table>
</section>
<section>
<h2>Recovered / Secondary Signals</h2>
<p class="muted">Signals observed during the run but not treated as the final strict outcome.</p>
<table>
<thead><tr><th>Signal</th><th>Cases</th><th>Share</th></tr></thead>
<tbody>{secondary_failure_rows}</tbody>
</table>
</section>
<section>
<h2>Result Filters</h2>
<div class="filters">
  <div><label for="pass-filter">Evaluator Pass / Fail</label><select id="pass-filter" onchange="applyFilters()"><option value="all">All</option><option value="PASS">PASS</option><option value="FAIL">FAIL</option></select></div>
  <div><label for="sql-execution-filter">SQL Execution</label><select id="sql-execution-filter" onchange="applyFilters()"><option value="all">All</option><option value="SUCCESS">SUCCESS</option><option value="FAIL">FAIL</option></select></div>
  <div><label for="failure-filter">Primary Failure</label><select id="failure-filter" onchange="applyFilters()">{self._render_select_options(failure_options)}</select></div>
  <div><label for="difficulty-filter">Difficulty</label><select id="difficulty-filter" onchange="applyFilters()">{self._render_select_options(difficulty_options)}</select></div>
  <div><label for="category-filter">Category</label><select id="category-filter" onchange="applyFilters()">{self._render_select_options(category_options)}</select></div>
  <div><label for="source-filter">Source</label><select id="source-filter" onchange="applyFilters()">{self._render_select_options(source_options)}</select></div>
  <div><label for="language-filter">Query Language</label><select id="language-filter" onchange="applyFilters()">{self._render_select_options(language_options)}</select></div>
  <div><label for="domain-filter">Business Domain</label><select id="domain-filter" onchange="applyFilters()">{self._render_select_options(domain_options)}</select></div>
</div>
<p class="muted"><span id="visible-count">{len(self.results)}</span> / {len(self.results)} visible</p>
</section>
<section>
<h2>Results</h2>
<table>
<thead>
<tr><th>Test Case</th><th>Database</th><th>Difficulty</th><th>Category</th><th>Source</th><th>Language</th><th>Business Domain</th><th>Evaluator Pass</th><th>SQL Execution</th><th>Schema Recall</th><th>Strict Result Correct</th><th>Business Result Correct</th><th>SQL Contract</th><th>First SQL Strict Correct</th><th>Tool Calls</th><th>Primary Failure</th><th>Score</th><th>Evaluators</th><th>Reason</th></tr>
</thead>
<tbody id="results-body">
{''.join(rows)}
</tbody>
</table>
</section>
<script>
function applyFilters() {{
  const passStatus = document.getElementById('pass-filter').value;
  const sqlExecution = document.getElementById('sql-execution-filter').value;
  const primaryFailure = document.getElementById('failure-filter').value;
  const difficulty = document.getElementById('difficulty-filter').value;
  const category = document.getElementById('category-filter').value;
  const source = document.getElementById('source-filter').value;
  const language = document.getElementById('language-filter').value;
  const businessDomain = document.getElementById('domain-filter').value;
  const rows = document.querySelectorAll('#results-body tr');
  let visible = 0;
  rows.forEach((row) => {{
    const matches = (!passStatus || passStatus === 'all' || row.dataset.passed === passStatus)
      && (!sqlExecution || sqlExecution === 'all' || row.dataset.sqlExecution === sqlExecution)
      && (!primaryFailure || primaryFailure === 'all' || row.dataset.primaryFailure === primaryFailure)
      && (!difficulty || difficulty === 'all' || row.dataset.difficulty === difficulty)
      && (!category || category === 'all' || row.dataset.category === category)
      && (!source || source === 'all' || row.dataset.source === source)
      && (!language || language === 'all' || row.dataset.queryLanguage === language)
      && (!businessDomain || businessDomain === 'all' || row.dataset.businessDomain === businessDomain);
    row.style.display = matches ? '' : 'none';
    if (matches) {{
      visible += 1;
    }}
  }});
  document.getElementById('visible-count').textContent = String(visible);
}}
window.addEventListener('DOMContentLoaded', applyFilters);
</script>
</body>
</html>
"""
        Path(path).write_text(html_text, encoding="utf-8")

    def _classification_value(self, result: EvaluationResult, field: str) -> str:
        labels = result.test_case.classification_dimensions()
        return labels.get(field, "unspecified")

    def _build_classification_summary(self, field: str) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[EvaluationResult]] = defaultdict(list)
        for result in self.results:
            grouped[self._classification_value(result, field)].append(result)

        rows = []
        for value, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
            count = len(items)
            pass_count = sum(1 for item in items if item.passed)
            rows.append(
                {
                    "value": value,
                    "count": count,
                    "pass_count": pass_count,
                    "pass_rate": pass_count / count if count else 0.0,
                    "average_score": sum(item.score for item in items) / count if count else 0.0,
                    "average_execution_time_ms": sum(
                        item.agent_result.execution_time_ms for item in items
                    )
                    / count
                    if count
                    else 0.0,
                }
            )
        return rows

    def _per_evaluator_results(self, result: EvaluationResult) -> Dict[str, EvaluationResult]:
        entries = result.metadata.get("per_evaluator_results", []) if result.metadata else []
        mapped: Dict[str, EvaluationResult] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            evaluator_name = entry.get("evaluator_name")
            payload = entry.get("result")
            if not evaluator_name or not isinstance(payload, dict):
                continue
            try:
                mapped[str(evaluator_name)] = EvaluationResult.model_validate(payload)
            except Exception:
                continue
        return mapped

    def _format_evaluator_breakdown(self, result: EvaluationResult) -> str:
        mapping = self._per_evaluator_results(result)
        ordered_names = list(self.evaluator_names)
        for name in mapping:
            if name not in ordered_names:
                ordered_names.append(name)

        parts = []
        for name in ordered_names:
            evaluator_result = mapping.get(name)
            if evaluator_result is None:
                continue
            parts.append(
                f"{name}: {evaluator_result.score:.2f} "
                f"({'PASS' if evaluator_result.passed else 'FAIL'})"
            )
        return " | ".join(parts)

    def _classification_options(self, field: str) -> List[str]:
        options = [row["value"] for row in self._build_classification_summary(field)]
        return options

    def _render_select_options(self, options: List[str]) -> str:
        rendered = ['<option value="all">All</option>']
        for option in options:
            rendered.append(
                f'<option value="{html.escape(option, quote=True)}">{html.escape(option)}</option>'
            )
        return "".join(rendered)

    def _boolean_label(self, value: Any) -> str:
        if value is None:
            return "N/A"
        return "YES" if bool(value) else "NO"

    def _classification_label(self, field: str) -> str:
        labels = {
            "difficulty": "Difficulty",
            "category": "Category",
            "source": "Source",
            "query_language": "Query Language",
            "business_domain": "Business Domain",
        }
        return labels.get(field, field.replace("_", " ").title())


class ComparisonReport(BaseModel):
    """Comparison across multiple evaluation reports."""

    reports: Dict[str, EvaluationReport]
    timestamp: datetime = Field(default_factory=_utc_now)

    def best_by_score(self) -> str:
        return max(self.reports.items(), key=lambda item: item[1].average_score())[0]
