# QueryMind Enterprise：项目归属与证据说明

## 项目定位

这是由 luyan9513 持续维护的企业经营数据问答项目，面向 AI 应用开发与 AI 产品方向。项目以开源 QueryMind 为技术基线，个人负责完整落地、国内模型链路、Schema 图修复、会话产品化、测试验收和后续版本路线。

上游仓库：<https://github.com/Tangxihong0922/QueryMind>

个人仓库：<https://github.com/luyan9513/QueryMind-Enterprise>

## 能力边界

### 上游已有能力

- Agent loop、工具注册与 Workflow。
- Schema Memory、SQL Governance 与 RLS 基础架构。
- FastAPI、Web Component、会话存储和评测框架。

### 个人完成的改造

| 改造 | 关键内容 | 提交 |
| --- | --- | --- |
| 多模型适配 | DeepSeek 主 Agent；硅基流动 Mem0 LLM/Embedding；Mem0 1.0.11 Base URL 和固定维度兼容 | `bb18ffc` |
| Schema 外键图 | 只读账号 `pg_catalog` 抽取；复合与跨 Schema 外键；Neo4j 表级和字段级关系 | `5bf0d93` |
| 会话产品化 | SSE 完成后刷新；一次性 LLM 标题；metadata 持久化；Workflow 消息；失败回退 | `a7c34eb` |
| 浏览器验证 | Playwright 开发依赖与桌面、移动端真实交互验证 | `0ad9f54` |
| Text2SQL 评测增强（v0.2） | 24 条中文经营问题；Schema Recall、全结果哈希、首次成功率、Token/成本、失败归因、脱敏导出和交互式报告 | `481c3ad` |
| 准确率与恢复优化（v0.2.1） | 业务等价比较、SQL 契约、离线重评分、一跳图扩展、Query Contract、温度配置、元数据循环恢复和真实模型对比 | `481c3ad` |
| Schema 证据计划与恢复（v0.3） | `submit_query_plan`、证据门禁、SQL 对齐、direct 精确检索、字段覆盖排序、完整图 Schema、窄范围计数口径检查和真实执行指标 | 本次 v0.3/v0.4 工作 |
| Agent 价值评测框架（v0.4） | S0/S1/S2 模式隔离、No-Agent 单次基线、模式安全恢复点、Wilson 区间、增量价值指标和公平性检查 | 本次 v0.3/v0.4 工作 |

## 已验证结果

- AdventureWorks：68 张表、456 个字段。
- 关键数据量：`person.person=19,972`、`production.product=504`、`sales.salesorderheader=31,465`、`sales.salesorderdetail=121,317`。
- Schema Memory：68 条 1024 维向量；91 条 `FK_TO`；91 条字段级 `REFERENCES`。
- 业务链路：完成 Schema hybrid retrieval、跨表 SQL、DataFrame、CSV、柱状图和中文总结。
- 评测数据：24 条中文经营问题，覆盖销售、商品、客户、采购、库存和人力；24/24 参考 SQL 在 PostgreSQL 只读事务中执行成功。
- v0.2 baseline：DeepSeek v4 Flash 完成 24/24；严格正确率 29.17%，首次严格 8.33%，Schema Recall 62.50%，平均工具 7.67，估算成本 0.064537 美元。
- v0.2.1 Pro 复测：完成 24/24；SQL 可执行率 100.00%，严格正确率 37.50%，业务等价 54.17%，首次严格 33.33%，Schema Recall 75.00%，平均工具 3.88，估算成本 0.145265 美元。
- v0.3 三题真实性冒烟：严格/业务/SQL Contract/Schema Recall/Agent 实际执行均为 100.00%，首次 SQL 正确率 66.67%，平均 Agent 延迟 28.30 秒；这是小样本恢复验证，不替代 24 题正式结论。
- 质量验证：Python 正式测试 `167 passed`；逐题 SQL、人工根因、失败实验和离线重评分证据已归档；浏览器无 console/page error；390px 移动端无页面级横向溢出。
- v0.4 首轮真实对照：同一 24 题中，S0/S1/S2 严格正确率分别为 20.83%/33.33%/50.00%，业务正确率为 25.00%/41.67%/54.17%；自动公平性检查通过。S2 P95 为 53.13 秒且错误但执行率仍为 41.67%，不能当作生产准确率承诺。

## 表达边界

- 可以写“个人主导项目，基于 QueryMind 开源基线持续开发”。
- 上游基础架构使用“打通并验证”，个人代码使用“设计、实现、修复”。
- 尚未完成生产部署、高并发压测、自动模型路由、每组 3 次稳定性验证和跨数据源准确率验证。
- 当前数字是本地运行与测试结果，不代表真实企业生产收益。

## 路线图

1. 先运行已实现的 Single-shot、Agent without Plan、Agent with Plan 公平 A/B/C，验证 Agent 的准确率增量、延迟和成本，不预设 Agent 一定更好。
2. 为第二套数据源建立独立语义合同、冻结题集、置信区间和准入门槛；未通过时只开放澄清或人工确认。
3. 根据 A/B 结果决定是否实现快速/Agent 双路径，以及是否需要独立 Plan Reviewer。
4. 增加云模型超时、重试、熔断和可观测指标。
5. 完成多用户 RLS 安全回归、敏感工具权限收敛和治理可观测页面。
