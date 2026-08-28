"""Audit benchmark question, reference SQL, and comparison-contract alignment."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from sqlglot import exp, parse_one

from QueryMind.core.evaluation import EvaluationDataset, SqlTestCase

_ORDER_CUE = re.compile(
    r"(按.+(?:排列|排序)|从高到低|从低到高|升序|降序|并列时|排名|前\s*\d+|最高的?\s*\d+|最低的?\s*\d+)"
)


@dataclass(frozen=True)
class ContractAuditIssue:
    case_id: str
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ContractAuditCase:
    case_id: str
    query: str
    projection_count: int
    projection_aliases: list[str]
    reference_has_order_by: bool
    question_has_order_cue: bool
    declared_order_sensitive: bool | None
    effective_order_sensitive: bool
    issues: list[ContractAuditIssue]


def _outer_select(expression: exp.Expression) -> exp.Select | None:
    if isinstance(expression, exp.Select):
        return expression
    if isinstance(expression, exp.Subquery):
        return _outer_select(expression.this)
    if isinstance(expression, (exp.Union, exp.Intersect, exp.Except)):
        return _outer_select(expression.this)
    return expression.find(exp.Select)


def _projection_aliases(select: exp.Select | None) -> list[str]:
    if select is None:
        return []
    aliases: list[str] = []
    for expression in select.expressions:
        alias = expression.alias_or_name
        aliases.append(str(alias or expression.sql()).strip().lower())
    return aliases


def audit_case(test_case: SqlTestCase) -> ContractAuditCase:
    parsed = parse_one(test_case.ground_truth_sql, read=test_case.dialect or None)
    select = _outer_select(parsed)
    aliases = _projection_aliases(select)
    reference_has_order_by = parsed.find(exp.Order) is not None
    question_has_order_cue = bool(_ORDER_CUE.search(test_case.query))
    declared_order_sensitive = test_case.result_comparison.order_sensitive
    effective_order_sensitive = (
        declared_order_sensitive
        if declared_order_sensitive is not None
        else reference_has_order_by
    )
    issues: list[ContractAuditIssue] = []
    contract = test_case.expected_sql_contract
    if contract is not None:
        if (
            contract.min_projection_count is not None
            and len(aliases) < contract.min_projection_count
        ):
            issues.append(
                ContractAuditIssue(
                    test_case.id,
                    "error",
                    "reference_below_projection_min",
                    "参考 SQL 返回 "
                    f"{len(aliases)} 列，少于合同下限 {contract.min_projection_count}。",
                )
            )
        if (
            contract.max_projection_count is not None
            and len(aliases) > contract.max_projection_count
        ):
            issues.append(
                ContractAuditIssue(
                    test_case.id,
                    "error",
                    "reference_above_projection_max",
                    "参考 SQL 返回 "
                    f"{len(aliases)} 列，多于合同上限 {contract.max_projection_count}。",
                )
            )
        missing_aliases = sorted(
            set(alias.lower() for alias in contract.required_projection_aliases)
            - set(aliases)
        )
        if missing_aliases:
            issues.append(
                ContractAuditIssue(
                    test_case.id,
                    "error",
                    "required_alias_missing_from_reference",
                    "参考 SQL 缺少合同要求的输出别名：" + ", ".join(missing_aliases),
                )
            )
        if contract.max_projection_count is None:
            issues.append(
                ContractAuditIssue(
                    test_case.id,
                    "info",
                    "projection_count_unbounded",
                    "SQL 合同未限制最大输出列数。",
                )
            )
    if effective_order_sensitive and not question_has_order_cue:
        issues.append(
            ContractAuditIssue(
                test_case.id,
                "warning",
                "implicit_order_requirement",
                "题面没有明确排序要求，但参考 SQL 使业务比较依赖行顺序。",
            )
        )
    if declared_order_sensitive is False and question_has_order_cue:
        issues.append(
            ContractAuditIssue(
                test_case.id,
                "info",
                "business_order_tolerance",
                "题面明确要求排序；严格指标仍检查顺序，业务指标容忍展示顺序差异。",
            )
        )
    return ContractAuditCase(
        case_id=test_case.id,
        query=test_case.query,
        projection_count=len(aliases),
        projection_aliases=aliases,
        reference_has_order_by=reference_has_order_by,
        question_has_order_cue=question_has_order_cue,
        declared_order_sensitive=declared_order_sensitive,
        effective_order_sensitive=effective_order_sensitive,
        issues=issues,
    )


def audit_dataset(dataset: EvaluationDataset) -> list[ContractAuditCase]:
    return [audit_case(test_case) for test_case in dataset]


def _summary(cases: Iterable[ContractAuditCase]) -> dict[str, int]:
    summary = {"case_count": 0, "error_count": 0, "warning_count": 0, "info_count": 0}
    for case in cases:
        summary["case_count"] += 1
        for issue in case.issues:
            summary[f"{issue.severity}_count"] += 1
    return summary


def _to_markdown(dataset: EvaluationDataset, cases: list[ContractAuditCase]) -> str:
    summary = _summary(cases)
    lines = [
        f"# {dataset.name} 评测口径审计",
        "",
        "本报告只检查题面、参考 SQL 和确定性比较合同是否对齐，不代表 Agent 准确率。",
        "",
        "## 摘要",
        "",
        f"- 题目数：{summary['case_count']}",
        f"- 错误：{summary['error_count']}",
        f"- 警告：{summary['warning_count']}",
        f"- 提示：{summary['info_count']}",
        "",
        "## 需要处理的错误和警告",
        "",
        "| 题号 | 级别 | 类型 | 说明 |",
        "|---|---|---|---|",
    ]
    material = [
        issue
        for case in cases
        for issue in case.issues
        if issue.severity in {"error", "warning"}
    ]
    if material:
        lines.extend(
            f"| {issue.case_id} | {issue.severity} | `{issue.code}` | {issue.message} |"
            for issue in material
        )
    else:
        lines.append("| - | - | - | 没有发现错误或警告。 |")
    lines.extend(
        [
            "",
            "## 逐题输出合同",
            "",
            "| 题号 | 参考列数 | 参考输出别名 | 参考含排序 | 题面含排序要求 | 有效顺序比较 |",
            "|---|---:|---|---|---|---|",
        ]
    )
    lines.extend(
        "| {case_id} | {projection_count} | {aliases} | {reference_order} | "
        "{question_order} | {effective_order} |".format(
            case_id=case.case_id,
            projection_count=case.projection_count,
            aliases=", ".join(f"`{alias}`" for alias in case.projection_aliases),
            reference_order="是" if case.reference_has_order_by else "否",
            question_order="是" if case.question_has_order_cue else "否",
            effective_order="是" if case.effective_order_sensitive else "否",
        )
        for case in cases
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = EvaluationDataset.from_yaml(args.dataset_path)
    cases = audit_dataset(dataset)
    payload = {
        "dataset_name": dataset.name,
        "summary": _summary(cases),
        "cases": [asdict(case) for case in cases],
    }
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    markdown = _to_markdown(dataset, cases)
    if args.output_markdown:
        output = Path(args.output_markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
