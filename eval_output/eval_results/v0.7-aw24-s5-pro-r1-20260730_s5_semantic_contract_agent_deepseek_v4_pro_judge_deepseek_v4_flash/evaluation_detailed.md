# 系统评测明细：AdventureWorks 中文经营问答微基准 v0.2

> 这份文档回答四个问题：系统准确率怎样、每题生成了什么 SQL、哪里错了、为什么错。
> 评测题里的参考 SQL 和 SQL 契约只用于离线评分，不会注入运行时 Agent。

## 一、总体结论

- 运行时间：2026-07-30T05:30:32.224566+00:00
- 运行状态：completed
- 样例数：24
- 严格结果准确率：29.17%
- 业务等价准确率：33.33%
- SQL 契约通过率：84.21%
- 评测器事后 SQL 可执行率：79.17%
- Agent 内部 SQL 执行成功率：58.33%
- 首条 SQL 严格正确率：36.84%
- Schema Recall：91.67%
- 平均 / P95 工具调用：5.54 / 8.85
- 平均 / P95 Agent 延迟：28.80s / 41.87s
- P50 / 最大 Agent 延迟：27.08s / 55.25s
- 自动回答覆盖率：58.33%
- 已自动回答业务准确率：57.14%
- 错误但已执行比例：25.00%
- 语义合同命中 / 引用题数：20 / 20
- 语义合同放行 / 拒绝尝试：17 / 3
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
| semantic_contract_failure | 11 | SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。 |
| success | 7 | 结果和题目约束均通过，没有发现确定性错误。 |
| result_mismatch | 5 | SQL 可以执行，但完整结果与参考结果不一致。 |
| query_contract_failure | 1 | SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。 |

## 三、逐题明细

### 1. aw_zh_001

- 用户问题：各销售区域累计订单金额是多少？按累计金额从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 21.39s
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
ORDER BY SUM(soh.totaldue) DESC
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 2. aw_zh_002

- 用户问题：统计 2024 年每个月的订单数和订单总金额，按月份排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 21.43s
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

### 3. aw_zh_003

- 用户问题：销量最高的 10 个商品是什么？返回商品名称和销售数量。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 19.43s
- 主要失败类型：success

参考 SQL：

```sql
SELECT p.name AS product_name, SUM(sod.orderqty) AS quantity_sold
FROM sales.salesorderdetail AS sod
JOIN production.product AS p ON p.productid = sod.productid
GROUP BY p.productid, p.name
ORDER BY quantity_sold DESC, product_name
LIMIT 10;

```

Agent 最终 SQL：

```sql
SELECT
    p.name AS "商品名称",
    SUM(sod.orderqty) AS "销售数量"
FROM adventureworks.sales.salesorderdetail sod
JOIN adventureworks.production.product p ON sod.productid = p.productid
GROUP BY p.name
ORDER BY SUM(sod.orderqty) DESC, p.name ASC
LIMIT 10
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 4. aw_zh_004

- 用户问题：各商品大类的销售收入是多少？按销售收入从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 27.32s
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

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 5. aw_zh_005

- 用户问题：订单金额最高的 10 位客户是谁？返回客户编号和累计订单金额。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 18.67s
- 主要失败类型：success

参考 SQL：

```sql
SELECT customerid, SUM(totaldue) AS total_sales
FROM sales.salesorderheader
GROUP BY customerid
ORDER BY total_sales DESC, customerid
LIMIT 10;

```

Agent 最终 SQL：

```sql
SELECT
    customerid AS "客户编号",
    SUM(totaldue) AS "累计订单金额"
FROM adventureworks.sales.salesorderheader
GROUP BY customerid
ORDER BY SUM(totaldue) DESC
LIMIT 10
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 6. aw_zh_006

- 用户问题：各区域有多少个不同客户下过订单？按客户数从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：6 次 / 30.78s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT st.name AS territory_name, COUNT(DISTINCT soh.customerid) AS customer_count
FROM sales.salesorderheader AS soh
JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
GROUP BY st.territoryid, st.name
ORDER BY customer_count DESC, territory_name;

```

Agent 最终 SQL：

```sql
SELECT
    st.name AS "区域名称",
    COUNT(DISTINCT soh.customerid) AS "不同客户数"
FROM adventureworks.sales.salesorderheader soh
JOIN adventureworks.sales.salesterritory st
    ON soh.territoryid = st.territoryid
GROUP BY st.name
ORDER BY "不同客户数" DESC
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 7. aw_zh_007

- 用户问题：线上订单和线下订单分别有多少笔、总金额是多少？
- 结论：严格正确=None；业务等价=None；SQL 契约=None；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 27.31s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT CASE WHEN onlineorderflag THEN 'online' ELSE 'offline' END AS order_channel,
       COUNT(*) AS order_count,
       SUM(totaldue) AS total_sales
FROM sales.salesorderheader
GROUP BY onlineorderflag
ORDER BY order_channel;

```

Agent 最终 SQL：

```sql
-- 未生成 SQL
```

哪里错了：

- 未发现确定性错误

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：No run_sql call was captured from the agent

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 8. aw_zh_008

- 用户问题：每种销售原因关联了多少张订单？按订单数从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：0.00%
- 工具调用 / Agent 延迟：7 次 / 38.28s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT sr.name AS reason_name, COUNT(DISTINCT sohsr.salesorderid) AS order_count
FROM sales.salesreason AS sr
JOIN sales.salesorderheadersalesreason AS sohsr ON sohsr.salesreasonid = sr.salesreasonid
GROUP BY sr.salesreasonid, sr.name
ORDER BY order_count DESC, reason_name;

```

Agent 最终 SQL：

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'sales'
  AND table_name LIKE '%reason%'
ORDER BY table_name
```

哪里错了：

- 缺少必要 SQL 结构：aggregation
- 缺少必要 SQL 结构：distinct
- 缺少必要 SQL 结构：group_by
- 缺少必要 SQL 结构：join
- 缺少必要字段：salesorderid
- 缺少必要字段：salesreasonid
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderheadersalesreason, sales.salesreason

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:aggregation, missing_feature:distinct, missing_feature:group_by, missing_feature:join, missing_column:salesorderid, missing_column:salesreasonid

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 9. aw_zh_009

- 用户问题：哪些商品从未产生过销售明细？返回商品编号和名称。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：50.00%
- 工具调用 / Agent 延迟：8 次 / 35.27s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT p.productid, p.name AS product_name
FROM production.product AS p
WHERE NOT EXISTS (
  SELECT 1 FROM sales.salesorderdetail AS sod WHERE sod.productid = p.productid
)
ORDER BY p.productid;

```

Agent 最终 SQL：

```sql
SELECT
    p.productid AS "商品编号",
    p.name AS "商品名称"
FROM adventureworks.production.product p
WHERE NOT EXISTS (
    SELECT 1
    FROM adventureworks.sales.salesorderdetail sod
    WHERE sod.productid = p.productid
)
ORDER BY p.productid
```

哪里错了：

- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderdetail

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 10. aw_zh_010

- 用户问题：统计各种颜色的商品数量，颜色为空的归为“未标注”。
- 结论：严格正确=None；业务等价=None；SQL 契约=None；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 22.50s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT COALESCE(color, '未标注') AS color_label, COUNT(*) AS product_count
FROM production.product
GROUP BY COALESCE(color, '未标注')
ORDER BY product_count DESC, color_label;

```

Agent 最终 SQL：

```sql
-- 未生成 SQL
```

哪里错了：

- 未发现确定性错误

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：No run_sql call was captured from the agent

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 11. aw_zh_011

- 用户问题：各库位当前库存总量是多少？按库存量从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 18.19s
- 主要失败类型：success

参考 SQL：

```sql
SELECT l.name AS location_name, SUM(pi.quantity) AS inventory_quantity
FROM production.productinventory AS pi
JOIN production.location AS l ON l.locationid = pi.locationid
GROUP BY l.locationid, l.name
ORDER BY inventory_quantity DESC, location_name;

```

Agent 最终 SQL：

```sql
SELECT
    loc.name AS "库位名称",
    COALESCE(SUM(inv.quantity), 0) AS "库存总量"
FROM adventureworks.production.productinventory inv
JOIN adventureworks.production.location loc
    ON inv.locationid = loc.locationid
GROUP BY loc.name
ORDER BY "库存总量" DESC
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 12. aw_zh_012

- 用户问题：列出库存低于商品安全库存量的商品，并汇总它们的当前库存。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：10 次 / 55.25s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT p.productid, p.name AS product_name, p.safetystocklevel,
       COALESCE(SUM(pi.quantity), 0) AS inventory_quantity
FROM production.product AS p
LEFT JOIN production.productinventory AS pi ON pi.productid = p.productid
GROUP BY p.productid, p.name, p.safetystocklevel
HAVING COALESCE(SUM(pi.quantity), 0) < p.safetystocklevel
ORDER BY p.productid;

```

Agent 最终 SQL：

```sql
SELECT
    p.name AS "商品名称",
    p.safetystocklevel AS "安全库存量",
    COALESCE(SUM(pi.quantity), 0) AS "当前库存"
FROM adventureworks.production.product p
LEFT JOIN adventureworks.production.productinventory pi
    ON p.productid = pi.productid
GROUP BY p.productid, p.name, p.safetystocklevel
HAVING COALESCE(SUM(pi.quantity), 0) < p.safetystocklevel
ORDER BY "当前库存" ASC, p.name ASC
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：Agent query returns 0-inventory products that are not in ground truth preview; ordering and column naming also differ.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 13. aw_zh_013

- 用户问题：各供应商的采购订单总金额是多少？只看仍处于启用状态的供应商。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：3 次 / 18.57s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT v.name AS vendor_name, SUM(poh.subtotal + poh.taxamt + poh.freight) AS purchase_total
FROM purchasing.vendor AS v
JOIN purchasing.purchaseorderheader AS poh ON poh.vendorid = v.businessentityid
WHERE v.activeflag = true
GROUP BY v.businessentityid, v.name
ORDER BY purchase_total DESC, vendor_name;

```

Agent 最终 SQL：

```sql
SELECT
    v.name AS "供应商名称",
    SUM(po.subtotal + po.taxamt + po.freight) AS "采购订单总金额"
FROM adventureworks.purchasing.vendor v
JOIN adventureworks.purchasing.purchaseorderheader po
    ON v.businessentityid = po.vendorid
WHERE v.activeflag = true
GROUP BY v.name
ORDER BY v.name
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：agent orders by vendor name ascending while ground truth orders by purchase_total descending then vendor name

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 14. aw_zh_014

- 用户问题：不同运输方式承运了多少张采购订单，平均运费是多少？
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：6 次 / 32.43s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT sm.name AS ship_method, COUNT(*) AS purchase_order_count,
       AVG(poh.freight) AS average_freight
FROM purchasing.purchaseorderheader AS poh
JOIN purchasing.shipmethod AS sm ON sm.shipmethodid = poh.shipmethodid
GROUP BY sm.shipmethodid, sm.name
ORDER BY purchase_order_count DESC, ship_method;

```

Agent 最终 SQL：

```sql
SELECT
    sm.name AS "运输方式",
    COUNT(*) AS "采购订单数量",
    AVG(poh.freight) AS "平均运费"
FROM adventureworks.purchasing.purchaseorderheader poh
JOIN adventureworks.purchasing.shipmethod sm
    ON poh.shipmethodid = sm.shipmethodid
GROUP BY sm.name
ORDER BY sm.name
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：Ground truth orders by purchase_order_count DESC, then ship_method; agent orders only by ship_method.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 15. aw_zh_015

- 用户问题：采购订单从下单到发货平均需要多少天？按供应商统计，只包含已经发货的订单。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：50.00%
- 工具调用 / Agent 延迟：5 次 / 31.63s
- 主要失败类型：query_contract_failure

参考 SQL：

```sql
SELECT v.name AS vendor_name, AVG(poh.shipdate::date - poh.orderdate::date) AS average_lead_days
FROM purchasing.purchaseorderheader AS poh
JOIN purchasing.vendor AS v ON v.businessentityid = poh.vendorid
WHERE poh.shipdate IS NOT NULL
GROUP BY v.businessentityid, v.name
ORDER BY average_lead_days DESC, vendor_name;

```

Agent 最终 SQL：

```sql
SELECT
    vendorid,
    AVG(CAST(shipdate AS DATE) - CAST(orderdate AS DATE)) AS average_lead_days
FROM adventureworks.purchasing.purchaseorderheader
WHERE shipdate IS NOT NULL
GROUP BY vendorid
ORDER BY vendorid
```

哪里错了：

- 缺少必要 SQL 结构：join
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：purchasing.vendor

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:join

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 16. aw_zh_016

- 用户问题：目前每个部门有多少在职员工？按在职人数从高到低排列。
- 结论：严格正确=False；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：9 次 / 42.45s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT d.name AS department_name, COUNT(DISTINCT edh.businessentityid) AS employee_count
FROM humanresources.employeedepartmenthistory AS edh
JOIN humanresources.department AS d ON d.departmentid = edh.departmentid
JOIN humanresources.employee AS e ON e.businessentityid = edh.businessentityid
WHERE edh.enddate IS NULL AND e.currentflag = true
GROUP BY d.departmentid, d.name
ORDER BY employee_count DESC, department_name;

```

Agent 最终 SQL：

```sql
WITH active_employees AS (
    SELECT edh.departmentid, edh.businessentityid
    FROM humanresources.employeedepartmenthistory edh
    JOIN humanresources.employee e
        ON edh.businessentityid = e.businessentityid
    WHERE edh.enddate IS NULL
      AND e.currentflag = true
)
SELECT
    d.name AS "部门名称",
    COUNT(DISTINCT ae.businessentityid) AS "在职员工数"
FROM humanresources.department d
LEFT JOIN active_employees ae
    ON d.departmentid = ae.departmentid
GROUP BY d.departmentid, d.name
ORDER BY "在职员工数" DESC
```

哪里错了：

- 原始结果表示不同，但按题目声明的精度、别名或顺序规则归一化后业务等价

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：The full result matched the dataset comparison policy after deterministic normalization.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 17. aw_zh_017

- 用户问题：按职位统计当前员工人数，只展示人数不少于 5 人的职位。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：6 次 / 23.84s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT jobtitle, COUNT(*) AS employee_count
FROM humanresources.employee
WHERE currentflag = true
GROUP BY jobtitle
HAVING COUNT(*) >= 5
ORDER BY employee_count DESC, jobtitle;

```

Agent 最终 SQL：

```sql
SELECT
    jobtitle AS "职位",
    COUNT(*) AS "员工人数"
FROM adventureworks.humanresources.employee
WHERE currentflag = true
GROUP BY jobtitle
HAVING COUNT(*) >= 5
ORDER BY "员工人数" DESC, jobtitle
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 18. aw_zh_018

- 用户问题：对销售人员按本年累计销售额排名，返回人员编号、销售额和名次。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：6 次 / 25.08s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT businessentityid, salesytd,
       DENSE_RANK() OVER (ORDER BY salesytd DESC) AS sales_rank
FROM sales.salesperson
ORDER BY sales_rank, businessentityid;

```

Agent 最终 SQL：

```sql
SELECT
    businessentityid AS "人员编号",
    salesytd AS "销售额",
    RANK() OVER (ORDER BY salesytd DESC) AS "名次"
FROM adventureworks.sales.salesperson
ORDER BY "名次"
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 19. aw_zh_019

- 用户问题：按年统计销售总额，并计算相对上一年的增长额。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：6 次 / 28.48s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
WITH yearly_sales AS (
  SELECT EXTRACT(YEAR FROM orderdate)::int AS sales_year, SUM(totaldue) AS total_sales
  FROM sales.salesorderheader
  GROUP BY EXTRACT(YEAR FROM orderdate)
)
SELECT sales_year, total_sales,
       total_sales - LAG(total_sales) OVER (ORDER BY sales_year) AS year_over_year_change
FROM yearly_sales
ORDER BY sales_year;

```

Agent 最终 SQL：

```sql
SELECT
    DATE_TRUNC('year', orderdate)::date AS 年份,
    SUM(totaldue) AS 销售总额,
    SUM(totaldue) - LAG(SUM(totaldue)) OVER (ORDER BY DATE_TRUNC('year', orderdate)::date) AS "相对上一年增长额"
FROM adventureworks.sales.salesorderheader
GROUP BY DATE_TRUNC('year', orderdate)::date
ORDER BY 年份
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Results match exactly; only column aliases (Chinese vs English) and date format (date vs year number) differ.

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 20. aw_zh_020

- 用户问题：统计 2024 年每个月的销售额以及从年初到当月的累计销售额。
- 结论：严格正确=None；业务等价=None；SQL 契约=None；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 25.99s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
WITH monthly_sales AS (
  SELECT DATE_TRUNC('month', orderdate)::date AS order_month, SUM(totaldue) AS monthly_sales
  FROM sales.salesorderheader
  WHERE orderdate >= DATE '2024-01-01' AND orderdate < DATE '2025-01-01'
  GROUP BY DATE_TRUNC('month', orderdate)
)
SELECT order_month, monthly_sales,
       SUM(monthly_sales) OVER (ORDER BY order_month) AS cumulative_sales
FROM monthly_sales
ORDER BY order_month;

```

Agent 最终 SQL：

```sql
-- 未生成 SQL
```

哪里错了：

- 未发现确定性错误

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：No run_sql call was captured from the agent

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 21. aw_zh_021

- 用户问题：按商品计算销售收入、估算成本和估算毛利，展示估算毛利最高的 10 个商品。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：4 次 / 25.96s
- 主要失败类型：success

参考 SQL：

```sql
SELECT p.name AS product_name,
       SUM(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount)) AS revenue,
       SUM(sod.orderqty * p.standardcost) AS estimated_cost,
       SUM(sod.orderqty * (sod.unitprice * (1 - sod.unitpricediscount) - p.standardcost)) AS estimated_margin
FROM sales.salesorderdetail AS sod
JOIN production.product AS p ON p.productid = sod.productid
GROUP BY p.productid, p.name
ORDER BY estimated_margin DESC, product_name
LIMIT 10;

```

Agent 最终 SQL：

```sql
SELECT
    p.name AS "商品名称",
    SUM(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount)) AS "销售收入",
    SUM(sod.orderqty * p.standardcost) AS "估算成本",
    SUM(sod.orderqty * (sod.unitprice * (1 - sod.unitpricediscount) - p.standardcost)) AS "估算毛利"
FROM adventureworks.sales.salesorderdetail sod
JOIN adventureworks.production.product p ON sod.productid = p.productid
GROUP BY p.name
ORDER BY "估算毛利" DESC
LIMIT 10;
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 22. aw_zh_022

- 用户问题：哪些销售区域本年累计销售额低于上一年？返回区域名称和两年的销售额。
- 结论：严格正确=None；业务等价=None；SQL 契约=None；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 26.84s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT name AS territory_name, salesytd, saleslastyear
FROM sales.salesterritory
WHERE salesytd < saleslastyear
ORDER BY name;

```

Agent 最终 SQL：

```sql
-- 未生成 SQL
```

哪里错了：

- 未发现确定性错误

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：No run_sql call was captured from the agent

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 23. aw_zh_023

- 用户问题：按国家或地区统计客户数量，依据客户所属销售区域计算。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：7 次 / 38.55s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT st.countryregioncode, COUNT(*) AS customer_count
FROM sales.customer AS c
JOIN sales.salesterritory AS st ON st.territoryid = c.territoryid
GROUP BY st.countryregioncode
ORDER BY customer_count DESC, st.countryregioncode;

```

Agent 最终 SQL：

```sql
SELECT
    cr.name AS "国家或地区",
    COUNT(*) AS "客户数量"
FROM adventureworks.sales.customer c
JOIN adventureworks.sales.salesterritory st
    ON c.territoryid = st.territoryid
JOIN adventureworks.person.countryregion cr
    ON st.countryregioncode = cr.countryregioncode
GROUP BY cr.name
ORDER BY "客户数量" DESC, cr.name
```

哪里错了：

- 缺少必要输出列：countryregioncode
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_projection_alias:countryregioncode

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。

### 24. aw_zh_024

- 用户问题：比较各销售区域 2023 年和 2024 年的订单金额。
- 结论：严格正确=None；业务等价=None；SQL 契约=None；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：5 次 / 35.61s
- 主要失败类型：semantic_contract_failure

参考 SQL：

```sql
SELECT st.name AS territory_name,
       SUM(CASE WHEN soh.orderdate >= DATE '2023-01-01' AND soh.orderdate < DATE '2024-01-01' THEN soh.totaldue ELSE 0 END) AS sales_2023,
       SUM(CASE WHEN soh.orderdate >= DATE '2024-01-01' AND soh.orderdate < DATE '2025-01-01' THEN soh.totaldue ELSE 0 END) AS sales_2024
FROM sales.salesorderheader AS soh
JOIN sales.salesterritory AS st ON st.territoryid = soh.territoryid
WHERE soh.orderdate >= DATE '2023-01-01' AND soh.orderdate < DATE '2025-01-01'
GROUP BY st.territoryid, st.name
ORDER BY territory_name;

```

Agent 最终 SQL：

```sql
-- 未生成 SQL
```

哪里错了：

- 未发现确定性错误

为什么错：

- SQL 未满足已引用指标的公式、来源表、字段或必需过滤，因此没有自动执行。
- 评测器说明：No run_sql call was captured from the agent

建议怎么改：

- 补齐或修订该数据源的指标合同；没有负责人确认的口径时应澄清，不要让模型猜。
