# 系统评测明细：AdventureWorks v0.3 Query Plan 冒烟集

> 这份文档回答四个问题：系统准确率怎样、每题生成了什么 SQL、哪里错了、为什么错。
> 评测题里的参考 SQL 和 SQL 契约只用于离线评分，不会注入运行时 Agent。

## 一、总体结论

- 运行时间：2026-07-30T05:43:04.837228+00:00
- 运行状态：completed
- 样例数：3
- 严格结果准确率：33.33%
- 业务等价准确率：33.33%
- SQL 契约通过率：66.67%
- 评测器事后 SQL 可执行率：100.00%
- Agent 内部 SQL 执行成功率：33.33%
- 首条 SQL 严格正确率：33.33%
- Schema Recall：58.33%
- 平均 / P95 工具调用：5.00 / 5.90
- 平均 / P95 Agent 延迟：28.52s / 33.14s
- P50 / 最大 Agent 延迟：28.59s / 33.64s
- 自动回答覆盖率：33.33%
- 已自动回答业务准确率：100.00%
- 错误但已执行比例：0.00%
- 语义合同命中 / 引用题数：3 / 3
- 语义合同放行 / 拒绝尝试：1 / 0
- 估算模型成本：未配置价格
- 每个严格 / 业务正确答案成本：无法计算 / 无法计算
- 同题重复运行一致率：没有重复运行

### 指标怎么理解

- 严格结果准确率：完整结果值、行数、列数和题目契约同时通过，最保守。
- 业务等价准确率：允许评测题显式声明的小数精度、同义值和顺序差异，但仍必须通过 SQL 契约。
- SQL 契约：检查必要聚合、分组、过滤字段、输出列等结构，防止错误 SQL 被宽松 Judge 误判为正确。
- 评测器事后 SQL 可执行率：评测器单独执行 Agent 最后尝试的 SQL；不代表 Agent 运行时放行并执行。
- Agent 内部 SQL 执行成功率：至少一次 `run_sql` 真正通过计划、治理和权限检查并执行成功。
- 已自动回答业务准确率：只在真正执行的题中统计正确率，必须和覆盖率一起看，不能靠大量拒答单独美化。
- Evaluator 通过率不是严格准确率；其中可能包含大模型 Judge 的判断，应结合上面三个确定性指标阅读。
- 任何指标都只代表当前数据、当前问题集和当前模型配置，不能证明换一套数据后仍然 100% 正确。

## 二、失败分布

| 失败类型 | 数量 | 大白话解释 |
|---|---:|---|
| query_contract_failure | 1 | SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。 |
| result_mismatch | 1 | SQL 可以执行，但完整结果与参考结果不一致。 |
| success | 1 | 结果和题目约束均通过，没有发现确定性错误。 |

## 三、逐题明细

### 1. aw_v03_smoke_001

- 用户问题：各销售区域累计订单金额是多少？按累计金额从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：0.00%
- 工具调用 / Agent 延迟：5 次 / 28.59s
- 主要失败类型：query_contract_failure

参考 SQL：

```sql
SELECT st.name AS territory_name, SUM(soh.totaldue) AS total_sales
FROM sales.salesorderheader AS soh
JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
GROUP BY st.territoryid, st.name
ORDER BY total_sales DESC, territory_name;

```

Agent 最终 SQL：

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'sales'
  AND table_name IN ('salesorderheader', 'salesterritory')
ORDER BY table_name, ordinal_position
```

哪里错了：

- 缺少必要 SQL 结构：aggregation
- 缺少必要 SQL 结构：group_by
- 缺少必要 SQL 结构：join
- 缺少必要字段：territoryid
- 缺少必要字段：totaldue
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderheader, sales.salesterritory

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:aggregation, missing_feature:group_by, missing_feature:join, missing_column:territoryid, missing_column:totaldue

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 2. aw_v03_smoke_002

- 用户问题：统计 2024 年每个月的订单数和订单总金额，按月份排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 23.32s
- 主要失败类型：success

参考 SQL：

```sql
SELECT DATE_TRUNC('month', orderdate)::date AS order_month,
       COUNT(*) AS order_count,
       SUM(totaldue) AS total_sales
FROM sales.salesorderheader
WHERE orderdate >= DATE '2024-01-01' AND orderdate < DATE '2025-01-01'
GROUP BY DATE_TRUNC('month', orderdate)
ORDER BY order_month;

```

Agent 最终 SQL：

```sql
SELECT
    DATE_TRUNC('month', orderdate)::date AS "月份",
    COUNT(*) AS "订单数",
    SUM(totaldue) AS "订单总金额"
FROM adventureworks.sales.salesorderheader
WHERE orderdate >= '2024-01-01'
  AND orderdate < '2025-01-01'
GROUP BY DATE_TRUNC('month', orderdate)::date
ORDER BY "月份"
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 3. aw_v03_smoke_004

- 用户问题：各商品大类的销售收入是多少？按销售收入从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：75.00%
- 工具调用 / Agent 延迟：6 次 / 33.64s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT pc.name AS category_name, SUM(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount)) AS revenue
FROM sales.salesorderdetail AS sod
JOIN production.product AS p ON p.productid = sod.productid
JOIN production.productsubcategory AS psc ON psc.productsubcategoryid = p.productsubcategoryid
JOIN production.productcategory AS pc ON pc.productcategoryid = psc.productcategoryid
GROUP BY pc.productcategoryid, pc.name
ORDER BY revenue DESC, category_name;

```

Agent 最终 SQL：

```sql
SELECT
    pc.name AS "商品大类",
    SUM(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount)) AS "销售收入"
FROM adventureworks.sales.salesorderdetail sod
JOIN adventureworks.production.product p ON sod.productid = p.productid
JOIN adventureworks.production.productsubcategory ps ON p.productsubcategoryid = ps.productsubcategoryid
JOIN adventureworks.production.productcategory pc ON ps.productcategoryid = pc.productcategoryid
GROUP BY pc.name
ORDER BY "销售收入" DESC
```

哪里错了：

- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderdetail

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。
