# 系统评测明细：AdventureWorks v0.3 Query Plan 冒烟集

> 这份文档回答四个问题：系统准确率怎样、每题生成了什么 SQL、哪里错了、为什么错。
> 评测题里的参考 SQL 和 SQL 契约只用于离线评分，不会注入运行时 Agent。

## 一、总体结论

- 运行时间：2026-07-30T05:08:55.269031+00:00
- 运行状态：completed
- 样例数：3
- 严格结果准确率：66.67%
- 业务等价准确率：66.67%
- SQL 契约通过率：100.00%
- 评测器事后 SQL 可执行率：100.00%
- Agent 内部 SQL 执行成功率：66.67%
- 首条 SQL 严格正确率：66.67%
- Schema Recall：100.00%
- 平均 / P95 工具调用：6.00 / 7.00
- 平均 / P95 Agent 延迟：29.43s / 36.06s
- P50 / 最大 Agent 延迟：30.01s / 36.73s
- 自动回答覆盖率：66.67%
- 已自动回答业务准确率：100.00%
- 错误但已执行比例：0.00%
- 语义合同命中 / 引用题数：3 / 3
- 语义合同放行 / 拒绝尝试：2 / 0
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
| success | 3 | 结果和题目约束均通过，没有发现确定性错误。 |

## 三、逐题明细

### 1. aw_v03_smoke_001

- 用户问题：各销售区域累计订单金额是多少？按累计金额从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：7 次 / 30.01s
- 主要失败类型：success

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
SELECT
    st.name AS "销售区域",
    SUM(soh.totaldue) AS "累计订单金额"
FROM adventureworks.sales.salesorderheader soh
JOIN adventureworks.sales.salesterritory st
    ON soh.territoryid = st.territoryid
GROUP BY st.name
ORDER BY "累计订单金额" DESC
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 2. aw_v03_smoke_002

- 用户问题：统计 2024 年每个月的订单数和订单总金额，按月份排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 21.54s
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
    DATE_TRUNC('month', orderdate)::date AS order_month,
    COUNT(*) AS order_count,
    SUM(totaldue) AS order_total
FROM adventureworks.sales.salesorderheader
WHERE orderdate >= '2024-01-01'
  AND orderdate < '2025-01-01'
GROUP BY DATE_TRUNC('month', orderdate)::date
ORDER BY order_month
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
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：7 次 / 36.73s
- 主要失败类型：success

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
JOIN adventureworks.production.productsubcategory psc ON p.productsubcategoryid = psc.productsubcategoryid
JOIN adventureworks.production.productcategory pc ON psc.productcategoryid = pc.productcategoryid
GROUP BY pc.name
ORDER BY "销售收入" DESC
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。
