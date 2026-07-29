# 系统评测明细：AdventureWorks 中文经营问答微基准 v0.2

> 这份文档回答四个问题：系统准确率怎样、每题生成了什么 SQL、哪里错了、为什么错。
> 评测题里的参考 SQL 和 SQL 契约只用于离线评分，不会注入运行时 Agent。

## 一、总体结论

- 运行时间：2026-07-29T05:26:31.050975+00:00
- 运行状态：completed
- 样例数：24
- 严格结果准确率：20.83%
- 业务等价准确率：25.00%
- SQL 契约通过率：33.33%
- 评测器事后 SQL 可执行率：87.50%
- Agent 内部 SQL 执行成功率：87.50%
- 首条 SQL 严格正确率：25.00%
- Schema Recall：76.74%
- 平均 / P95 工具调用：2.00 / 2.00
- 平均 / P95 Agent 延迟：2.01s / 2.65s
- P50 / 最大 Agent 延迟：1.96s / 2.74s
- 自动回答覆盖率：87.50%
- 错误但已执行比例：62.50%
- 估算模型成本：$0.023774
- 每个严格 / 业务正确答案成本：$0.004755 / $0.003962
- 同题重复运行一致率：没有重复运行

### 指标怎么理解

- 严格结果准确率：完整结果值、行数、列数和题目契约同时通过，最保守。
- 业务等价准确率：允许评测题显式声明的小数精度、同义值和顺序差异，但仍必须通过 SQL 契约。
- SQL 契约：检查必要聚合、分组、过滤字段、输出列等结构，防止错误 SQL 被宽松 Judge 误判为正确。
- 评测器事后 SQL 可执行率：评测器单独执行 Agent 最后尝试的 SQL；不代表 Agent 运行时放行并执行。
- Agent 内部 SQL 执行成功率：至少一次 `run_sql` 真正通过计划、治理和权限检查并执行成功。
- Evaluator 通过率不是严格准确率；其中可能包含大模型 Judge 的判断，应结合上面三个确定性指标阅读。
- 任何指标都只代表当前数据、当前问题集和当前模型配置，不能证明换一套数据后仍然 100% 正确。

## 二、失败分布

| 失败类型 | 数量 | 大白话解释 |
|---|---:|---|
| query_contract_failure | 16 | SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。 |
| success | 5 | 结果和题目约束均通过，没有发现确定性错误。 |
| result_mismatch | 3 | SQL 可以执行，但完整结果与参考结果不一致。 |

## 三、逐题明细

### 1. aw_zh_001

- 用户问题：各销售区域累计订单金额是多少？按累计金额从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 2.67s
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
SELECT t.name AS territory_name, SUM(h.totaldue) AS cumulative_order_amount FROM adventureworks.sales.salesorderheader h JOIN adventureworks.sales.salesterritory t ON h.territoryid = t.territoryid GROUP BY t.name ORDER BY cumulative_order_amount DESC
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
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：0.00%
- 工具调用 / Agent 延迟：2 次 / 1.97s
- 主要失败类型：query_contract_failure

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
SELECT EXTRACT(MONTH FROM transactiondate) AS month, COUNT(*) AS order_count, SUM(actualcost * quantity) AS total_amount FROM adventureworks.production.transactionhistory WHERE transactiontype = 'S' AND EXTRACT(YEAR FROM transactiondate) = 2024 GROUP BY EXTRACT(MONTH FROM transactiondate) ORDER BY month
```

哪里错了：

- 缺少必要字段：orderdate
- 缺少必要字段：totaldue
- 缺少必要过滤字段：orderdate
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderheader

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_column:orderdate, missing_column:totaldue, missing_filter_column:orderdate

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 3. aw_zh_003

- 用户问题：销量最高的 10 个商品是什么？返回商品名称和销售数量。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：0.00%
- 工具调用 / Agent 延迟：2 次 / 2.05s
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
SELECT p.name AS product_name, SUM(sod.orderqty) AS total_sales_quantity FROM adventureworks.sales.salesorderdetail sod JOIN adventureworks.production.product p ON sod.productid = p.productid JOIN adventureworks.sales.salesorderheader soh ON sod.salesorderid = soh.salesorderid GROUP BY p.name ORDER BY total_sales_quantity DESC LIMIT 10
```

哪里错了：

- 首条 SQL 前未召回必要表：production.product, sales.salesorderdetail

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 4. aw_zh_004

- 用户问题：各商品大类的销售收入是多少？按销售收入从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：25.00%
- 工具调用 / Agent 延迟：2 次 / 2.08s
- 主要失败类型：query_contract_failure

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
SELECT pc.Name AS ProductCategory, SUM(sod.LineTotal) AS SalesRevenue FROM Sales.SalesOrderDetail sod JOIN Production.Product p ON sod.ProductID = p.ProductID JOIN Production.ProductSubcategory ps ON p.ProductSubcategoryID = ps.ProductSubcategoryID JOIN Production.ProductCategory pc ON ps.ProductCategoryID = pc.ProductCategoryID GROUP BY pc.Name ORDER BY SalesRevenue DESC
```

哪里错了：

- 缺少必要字段：orderqty
- 缺少必要字段：unitprice
- 缺少必要字段：unitpricediscount
- SQL 执行失败：column sod.linetotal does not exist
LINE 1: SELECT pc.Name AS ProductCategory, SUM(sod.LineTotal) AS Sal...
                                               ^

- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：production.product, production.productcategory, sales.salesorderdetail

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Agent SQL failed to execute: column sod.linetotal does not exist
LINE 1: SELECT pc.Name AS ProductCategory, SUM(sod.LineTotal) AS Sal...
                                               ^


建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 5. aw_zh_005

- 用户问题：订单金额最高的 10 位客户是谁？返回客户编号和累计订单金额。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.51s
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
SELECT soh.customerid, SUM(soh.totaldue) AS total_amount FROM adventureworks.sales.salesorderheader soh GROUP BY soh.customerid ORDER BY total_amount DESC LIMIT 10
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
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 2.17s
- 主要失败类型：result_mismatch

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
SELECT t.name AS territory_name, COUNT(DISTINCT c.customerid) AS customer_count FROM adventureworks.sales.salesterritory t LEFT JOIN adventureworks.sales.customer c ON t.territoryid = c.territoryid GROUP BY t.territoryid, t.name ORDER BY customer_count DESC
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：The agent uses a LEFT JOIN from salesterritory to customer and counts distinct customerid from customer, which yields higher counts than the ground truth that counts distinct customerid from salesorderheader after an INNER JOIN. This gives a materially different count of customers per territory because the ground truth counts only customers who placed orders, while the agent counts all customers (including those without orders).

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 7. aw_zh_007

- 用户问题：线上订单和线下订单分别有多少笔、总金额是多少？
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.74s
- 主要失败类型：query_contract_failure

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
SELECT onlineorderflag, COUNT(*) AS order_count, SUM(totaldue) AS total_amount FROM adventureworks.sales.salesorderheader GROUP BY onlineorderflag;
```

哪里错了：

- 缺少必要 SQL 结构：case_when
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:case_when

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 8. aw_zh_008

- 用户问题：每种销售原因关联了多少张订单？按订单数从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.90s
- 主要失败类型：success

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
SELECT sr.name AS sales_reason, COUNT(DISTINCT sohsr.salesorderid) AS order_count FROM adventureworks.sales.salesreason sr JOIN adventureworks.sales.salesorderheadersalesreason sohsr ON sr.salesreasonid = sohsr.salesreasonid GROUP BY sr.name ORDER BY order_count DESC
```

哪里错了：

- 未发现确定性错误

为什么错：

- 结果和题目约束均通过，没有发现确定性错误。
- 评测器说明：Full result fingerprint matched the ground truth result.

建议怎么改：

- 保留为回归样例，防止后续优化造成退化。

### 9. aw_zh_009

- 用户问题：哪些商品从未产生过销售明细？返回商品编号和名称。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：50.00%
- 工具调用 / Agent 延迟：2 次 / 1.52s
- 主要失败类型：query_contract_failure

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
SELECT p.productid, p.name FROM adventureworks.production.product p WHERE p.productid NOT IN (SELECT DISTINCT sod.productid FROM adventureworks.sales.salesorderdetail sod);
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderdetail

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 10. aw_zh_010

- 用户问题：统计各种颜色的商品数量，颜色为空的归为“未标注”。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.60s
- 主要失败类型：query_contract_failure

参考 SQL：

```sql
SELECT COALESCE(color, '未标注') AS color_label, COUNT(*) AS product_count
FROM production.product
GROUP BY COALESCE(color, '未标注')
ORDER BY product_count DESC, color_label;

```

Agent 最终 SQL：

```sql
SELECT COALESCE(color, '未标注') AS color_category, COUNT(*) AS product_count FROM adventureworks.production.product GROUP BY COALESCE(color, '未标注')
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 11. aw_zh_011

- 用户问题：各库位当前库存总量是多少？按库存量从高到低排列。
- 结论：严格正确=True；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.96s
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
SELECT l.name AS location_name, SUM(pi.quantity) AS total_quantity FROM adventureworks.production.productinventory pi JOIN adventureworks.production.location l ON pi.locationid = l.locationid GROUP BY l.name ORDER BY total_quantity DESC
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
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.75s
- 主要失败类型：query_contract_failure

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
SELECT p.productid, p.name, p.safetystocklevel, SUM(pi.quantity) AS current_stock FROM adventureworks.production.product p JOIN adventureworks.production.productinventory pi ON p.productid = pi.productid GROUP BY p.productid, p.name, p.safetystocklevel HAVING SUM(pi.quantity) < p.safetystocklevel
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 13. aw_zh_013

- 用户问题：各供应商的采购订单总金额是多少？只看仍处于启用状态的供应商。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.71s
- 主要失败类型：query_contract_failure

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
SELECT v.name AS vendor_name, SUM(poh.subtotal) AS total_purchase_amount FROM adventureworks.purchasing.vendor v JOIN adventureworks.purchasing.purchaseorderheader poh ON v.businessentityid = poh.vendorid WHERE v.activeflag = true GROUP BY v.name
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 缺少必要字段：freight
- 缺少必要字段：taxamt
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by, missing_column:freight, missing_column:taxamt

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 14. aw_zh_014

- 用户问题：不同运输方式承运了多少张采购订单，平均运费是多少？
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：50.00%
- 工具调用 / Agent 延迟：2 次 / 2.74s
- 主要失败类型：query_contract_failure

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
SELECT sm.name AS ship_method, COUNT(DISTINCT poh.purchaseorderid) AS order_count, AVG(poh.freight) AS avg_freight FROM adventureworks.purchasing.purchaseorderheader poh JOIN adventureworks.purchasing.shipmethod sm ON poh.shipmethodid = sm.shipmethodid GROUP BY sm.name
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：purchasing.purchaseorderheader

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 15. aw_zh_015

- 用户问题：采购订单从下单到发货平均需要多少天？按供应商统计，只包含已经发货的订单。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.89s
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
SELECT v.name AS vendor_name, AVG(EXTRACT(DAY FROM (h.shipdate - h.orderdate))) AS avg_days_to_ship FROM purchasing.purchaseorderheader h JOIN purchasing.vendor v ON h.vendorid = v.businessentityid WHERE h.shipdate IS NOT NULL AND h.status = 4 GROUP BY v.name
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 16. aw_zh_016

- 用户问题：目前每个部门有多少在职员工？按在职人数从高到低排列。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：66.67%
- 工具调用 / Agent 延迟：2 次 / 2.28s
- 主要失败类型：query_contract_failure

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
SELECT d.name AS department, COUNT(e.businessentityid) AS employee_count FROM adventureworks.humanresources.employeedepartmenthistory edh JOIN adventureworks.humanresources.department d ON edh.departmentid = d.departmentid JOIN adventureworks.humanresources.employee e ON edh.businessentityid = e.businessentityid WHERE edh.enddate IS NULL AND e.currentflag = true GROUP BY d.name ORDER BY employee_count DESC
```

哪里错了：

- 缺少必要 SQL 结构：distinct
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：humanresources.department

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:distinct

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 17. aw_zh_017

- 用户问题：按职位统计当前员工人数，只展示人数不少于 5 人的职位。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.89s
- 主要失败类型：query_contract_failure

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
SELECT jobtitle, COUNT(*) AS employee_count FROM adventureworks.humanresources.employee WHERE currentflag = true GROUP BY jobtitle HAVING COUNT(*) >= 5
```

哪里错了：

- 缺少必要 SQL 结构：order_by
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:order_by

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 18. aw_zh_018

- 用户问题：对销售人员按本年累计销售额排名，返回人员编号、销售额和名次。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：0.00%
- 工具调用 / Agent 延迟：2 次 / 2.04s
- 主要失败类型：query_contract_failure

参考 SQL：

```sql
SELECT businessentityid, salesytd,
       DENSE_RANK() OVER (ORDER BY salesytd DESC) AS sales_rank
FROM sales.salesperson
ORDER BY sales_rank, businessentityid;

```

Agent 最终 SQL：

```sql
SELECT soh.salespersonid AS salesperson_id, SUM(soh.totaldue) AS total_sales, RANK() OVER (ORDER BY SUM(soh.totaldue) DESC) AS rank FROM adventureworks.sales.salesorderheader soh WHERE soh.salespersonid IS NOT NULL AND EXTRACT(YEAR FROM soh.modifieddate) = EXTRACT(YEAR FROM CURRENT_DATE) GROUP BY soh.salespersonid ORDER BY rank
```

哪里错了：

- 缺少必要字段：businessentityid
- 缺少必要字段：salesytd
- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesperson

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_column:businessentityid, missing_column:salesytd

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 19. aw_zh_019

- 用户问题：按年统计销售总额，并计算相对上一年的增长额。
- 结论：严格正确=False；业务等价=False；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.85s
- 主要失败类型：result_mismatch

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
SELECT EXTRACT(YEAR FROM orderdate) AS year, SUM(totaldue) AS total_sales, SUM(totaldue) - LAG(SUM(totaldue)) OVER (ORDER BY EXTRACT(YEAR FROM orderdate)) AS growth FROM adventureworks.sales.salesorderheader GROUP BY EXTRACT(YEAR FROM orderdate) ORDER BY year
```

哪里错了：

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：The agent SQL produces identical rows as ground truth; only column aliases and whitespace differ.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 20. aw_zh_020

- 用户问题：统计 2024 年每个月的销售额以及从年初到当月的累计销售额。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 2.49s
- 主要失败类型：query_contract_failure

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
SELECT EXTRACT(MONTH FROM h.orderdate) AS month, SUM(d.linetotal) AS monthly_sales, SUM(SUM(d.linetotal)) OVER (ORDER BY EXTRACT(MONTH FROM h.orderdate)) AS cumulative_sales FROM adventureworks.sales.salesorderheader h JOIN adventureworks.sales.salesorderdetail d ON h.salesorderid = d.salesorderid WHERE EXTRACT(YEAR FROM h.orderdate) = 2024 GROUP BY EXTRACT(MONTH FROM h.orderdate) ORDER BY month;
```

哪里错了：

- 缺少必要字段：totaldue
- SQL 执行失败：column d.linetotal does not exist
LINE 1: ...ECT EXTRACT(MONTH FROM h.orderdate) AS month, SUM(d.linetota...
                                                             ^

- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Agent SQL failed to execute: column d.linetotal does not exist
LINE 1: ...ECT EXTRACT(MONTH FROM h.orderdate) AS month, SUM(d.linetota...
                                                             ^


建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 21. aw_zh_021

- 用户问题：按商品计算销售收入、估算成本和估算毛利，展示估算毛利最高的 10 个商品。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=False；Agent 实际执行=False
- Schema Recall：50.00%
- 工具调用 / Agent 延迟：2 次 / 2.29s
- 主要失败类型：query_contract_failure

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
SELECT p.productid, p.name, SUM(sod.linetotal) AS sales_revenue, SUM(sod.orderqty * p.standardcost) AS estimated_cost, SUM(sod.linetotal) - SUM(sod.orderqty * p.standardcost) AS estimated_gross_profit FROM adventureworks.production.product p JOIN adventureworks.sales.salesorderdetail sod ON p.productid = sod.productid GROUP BY p.productid, p.name ORDER BY estimated_gross_profit DESC LIMIT 10
```

哪里错了：

- 缺少必要字段：unitprice
- 缺少必要字段：unitpricediscount
- 输出列过多：5>4
- SQL 执行失败：column sod.linetotal does not exist
LINE 1: SELECT p.productid, p.name, SUM(sod.linetotal) AS sales_reve...
                                        ^

- 完整结果与参考 SQL 不一致
- 首条 SQL 前未召回必要表：sales.salesorderdetail

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Agent SQL failed to execute: column sod.linetotal does not exist
LINE 1: SELECT p.productid, p.name, SUM(sod.linetotal) AS sales_reve...
                                        ^


建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 22. aw_zh_022

- 用户问题：哪些销售区域本年累计销售额低于上一年？返回区域名称和两年的销售额。
- 结论：严格正确=False；业务等价=True；SQL 契约=True；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.65s
- 主要失败类型：result_mismatch

参考 SQL：

```sql
SELECT name AS territory_name, salesytd, saleslastyear
FROM sales.salesterritory
WHERE salesytd < saleslastyear
ORDER BY name;

```

Agent 最终 SQL：

```sql
SELECT t.name AS territory_name, t.salesytd AS current_year_sales, t.saleslastyear AS previous_year_sales FROM adventureworks.sales.salesterritory t WHERE t.salesytd < t.saleslastyear
```

哪里错了：

- 原始结果表示不同，但按题目声明的精度、别名或顺序规则归一化后业务等价

为什么错：

- SQL 可以执行，但完整结果与参考结果不一致。
- 评测器说明：The full result matched the dataset comparison policy after deterministic normalization.

建议怎么改：

- 对照参考 SQL 检查聚合口径、JOIN 基数、过滤条件、排序和数值精度。

### 23. aw_zh_023

- 用户问题：按国家或地区统计客户数量，依据客户所属销售区域计算。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 1.99s
- 主要失败类型：query_contract_failure

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
SELECT cr.name AS country_region, COUNT(DISTINCT c.customerid) AS customer_count FROM adventureworks.sales.customer c JOIN adventureworks.sales.salesterritory st ON c.territoryid = st.territoryid JOIN adventureworks.person.countryregion cr ON st.countryregioncode = cr.countryregioncode GROUP BY cr.name ORDER BY cr.name
```

哪里错了：

- 缺少必要输出列：countryregioncode
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_projection_alias:countryregioncode

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。

### 24. aw_zh_024

- 用户问题：比较各销售区域 2023 年和 2024 年的订单金额。
- 结论：严格正确=False；业务等价=False；SQL 契约=False；评测器事后可执行=True；Agent 实际执行=True
- Schema Recall：100.00%
- 工具调用 / Agent 延迟：2 次 / 2.53s
- 主要失败类型：query_contract_failure

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
SELECT st.name AS territory_name, EXTRACT(YEAR FROM soh.modifieddate) AS order_year, SUM(soh.totaldue) AS total_order_amount FROM adventureworks.sales.salesorderheader soh JOIN adventureworks.sales.salesterritory st ON soh.territoryid = st.territoryid WHERE EXTRACT(YEAR FROM soh.modifieddate) IN (2023, 2024) GROUP BY st.name, EXTRACT(YEAR FROM soh.modifieddate) ORDER BY st.name, order_year
```

哪里错了：

- 缺少必要 SQL 结构：case_when
- 缺少必要字段：orderdate
- 缺少必要过滤字段：orderdate
- 缺少必要输出列：sales_2023
- 缺少必要输出列：sales_2024
- 完整结果与参考 SQL 不一致

为什么错：

- SQL 虽可能执行，但违反了题目声明的结构或业务口径约束。
- 评测器说明：Generated SQL violated the dataset SQL contract: missing_feature:case_when, missing_column:orderdate, missing_filter_column:orderdate, missing_projection_alias:sales_2023, missing_projection_alias:sales_2024

建议怎么改：

- 重新核对指标、粒度、过滤条件、时间范围、连接路径和输出列，再生成 SQL。
