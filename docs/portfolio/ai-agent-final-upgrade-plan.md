# QueryMind AI Agent 最终整合改造方案

> 审计日期：2026-08-26  
> 适用仓库：`QueryMind-personal`  
> 当前代码基线：`main` / `9e4068a`，工作区有大量未提交改动  
> 当前总状态：**Chinook NOT READY；AdventureWorks 仅限 24 题跨源回归 READY**

## 0. 结论和当前续接点

附件提出的方向大多有价值，但不能按“从零搭一套 Agent 平台”的顺序照搬。仓库已经有稳定 System Prompt、Runtime Notice、Schema/Agent Memory、Semantic Contract、Query Plan、RLS、SQL Governance、评测框架、Prometheus、Audit，以及一套正在实现中的单进程 Run/Event。当前最重要的问题不是能力数量，而是 Chinook 业务正确率、错误但成功执行（wrong-but-executed）和重复一致性仍未过门槛。

最终顺序是：

1. **P0 继续 v0.10.1 准确率主线**，先把重复运行失败账本自动化，再只用 development 集修复通用机制。
2. **P0 补真实结果校验和证据关联**，不能把“SQL 执行成功”或“回答生成完毕”叫作 `result.validated`。
3. **P1 扩展现有 Context、Run/Event 和可观测性抽象**，不新建第二套框架。
4. **P2 有证据再做 Skill、MCP 和平台化**；Reviewer/多 Agent 默认取消，新方言、通用 RAG、Kubernetes 延后。

本轮“当前续接阶段”定义为 **QUALITY-01A：冻结失败账本自动化**。它只读取现有三轮 JSON 报告，给现有 `BenchmarkQualityAssessment` 增加稳定失败、波动失败和分割集指标，不改冻结题集、参考 SQL、数据库、模型配置或运行时行为。该阶段的退出证据是：同一批历史报告的原有总指标完全不变，新增账本可重复得到已记录的稳定/波动样本。

## 1. 审计范围、证据级别和工作区保护

### 1.1 已读范围

- 仓库没有发现适用的 `AGENTS.md` 或 `CLAUDE.md`；本任务遵守会话中给出的全局项目规则。
- 已读根目录 `README.md`、`README_zh.md`、`CHANGELOG_PORTFOLIO.md`。
- 已读 `docs/portfolio/` 的 v0.3 至 v0.10、产品范围、Ownership/Evidence、Benchmark Admission 文档，以及 `docs/adr/0001-single-agent-governed-runtime.md`。
- 已读仓库外同项目证据目录中的 v0.10 实施日志、v0.10.1 准确率优化日志、步骤 1 至 6 执行记录、最终双数据源评测、Chinook/AdventureWorks 准入、模式对比、勘误、运行手册和风险记录。
- 已追踪 UI/API、Agent、Context、Schema Retrieval、Query Plan、Semantic Contract、RLS/Governance、SQL 执行、Recovery、Evaluation、Run/Event 和 SSE 的真实调用链。

### 1.2 证据级别

| 级别 | 含义 | 本文用法 |
|---|---|---|
| 当前代码事实 | 当前工作区可直接看到的实现 | 可以描述代码存在，但未运行前不说“本轮已验证” |
| 历史重复验证 | 冻结数据、固定配置和归档报告可复核 | 可以报告当时结果，必须带范围和日期 |
| 本轮离线验证 | 本轮实际执行且不调用模型/数据库的测试 | 可以作为局部实现证据 |
| 仅计划 | 文档目标或接口草案，代码链未闭环 | 不能写进简历完成项 |
| 负结果 | 同配置实验表明收益为负或风险更高 | 必须保留，不能选择性删除 |

### 1.3 Git 和用户修改保护

- `main` 与 `origin/main` 当前均指向 `9e4068a`。
- 审计时有 45 个已修改文件和 6 个未跟踪文件，约 2904 行新增、249 行删除；这些都视为用户正在进行的工作。
- 未跟踪内容包括 AdventureWorks admission profile、Run executor、Benchmark 审计脚本、Chinook v0.10 数据文件及相关测试。
- 本方案不要求清理、覆盖或回退这些改动；后续每次只触碰阶段清单中的文件。
- 禁止执行 `git add`、`commit`、`push`、`merge`、`rebase`、`reset`。

## 2. 当前真实能力和代码链路

### 2.1 请求到证据的真实链路

```text
用户问题
→ POST /api/querymind/v1/chat_sse 或 Run API
→ ChatHandler / AgentRunExecutor
→ Agent.send_message
→ ContextEnricher + Runtime Context Notice + Agent Memory
→ Tool schema / SchemaRetrieve
→ SubmitQueryPlan
→ Semantic Contract / SQL semantic checks
→ Tool Registry
→ 注入防护 → 复杂度 → Query Plan → Semantic Contract
  → 可选 Reviewer → SQL 语义 → Territory RLS → SQL Governance
→ RunSql → PostgreSQL → CSV/DataFrame/回答
→ metadata/query-plan recovery（结构化 recovery 默认关闭）
→ Evaluation process events 或 Run/Event/SSE
```

边界必须说清：

- `RunSql` 当前主要保证只读执行、治理和零行提示；没有独立的结果形状、粒度、数量或业务不变量校验器。
- 当前 Run executor 在聊天流程完成后发出 `result.validated`，这不等于业务结果已经验证，后续必须改名或在真实校验通过后再发。
- FileSystem/Memory Run Store、后台 `asyncio` executor、审批、取消和 SSE 属于单进程实现；它们不是多实例队列，也没有 lease、fencing token 或数据库事务恢复。
- Evaluation 的 process trace 是评测证据；Prometheus 是聚合运行指标；Audit 是合规审计记录；它们都不等于跨进程 OpenTelemetry Trace。

### 2.2 已有抽象必须复用

- 业务语义真相：`Semantic Contract`，按数据源和版本管理。
- Schema 证据：Schema Memory、Schema Retrieval 元数据和 Query Plan。
- 权限真相：服务端解析的用户/租户/数据库范围、Tool Registry、RLS 和 SQL Governance。
- Agent 上下文：稳定 System Prompt、Runtime Notice、Context Enricher、Agent Memory 和对话消息。
- Run 持久化边界：`AgentRunStore`；PostgreSQL 实现应作为新适配器接入。
- 评测决策：`BenchmarkAdmissionProfile`、`BenchmarkQualityAssessment` 和归档 `EvaluationReport`。

## 3. 数据集、指标和 READY 边界

### 3.1 冻结数据

| 数据源 | 规模 | 分割/范围 | 冻结证据 | 当前用途 |
|---|---:|---|---|---|
| Chinook | 11 表、64 列、11 外键、15,607 行；100 题 | development 60 / test 20 / holdout 20；难度 30/40/30 | 数据集 SHA-256 `0f440b…e5815090`；快照 `chinook_1_4_5_20260730` / `chinook_schema_20260730` | 生产候选准入 |
| AdventureWorks | 24 题 | 14 easy / 10 medium，跨 6 个业务域 | 数据集 SHA-256 `17da891…eec03`；固定数据/模式快照 | 小样本跨源回归，不代表生产 |

参考 SQL 当前两套都已在历史准入中 100% 执行成功。任何修改数据集、参考 SQL、快照或 Semantic Contract 版本，都必须新建版本、重跑参考 SQL 审计，并禁止与旧结果直接比较。

### 3.2 v0.10.1 历史正式结果

| 数据源 | 配置 | 业务正确率 | wrong-but-executed | 一致性 | 最差 P95 | 结论 |
|---|---|---:|---:|---:|---:|---|
| Chinook | S5，100 题 × 3，DeepSeek v4 Flash，temperature 0，并发 2 | 76% / 84% / 77%，均值 **79.00%** | **10.33%** | **83.00%** | 22.91 秒 | **NOT READY** |
| AdventureWorks | S5，24 题 × 3，同类固定配置 | 均值 **86.11%** | **6.94%** | **83.33%** | 29.41 秒 | 仅该 24 题 profile **READY** |

Chinook 分割集历史均值为 development 87.22%、test 70.00%、holdout 63.33%，说明泛化差距明显。三轮中稳定错误 13 题：024、044、054、063、067、071、077、079、081、093、096、099、100；波动 17 题：016、026、036、039、041、043、046、047、056、057、059、064、070、085、088、089、092。包含 test/holdout 的编号只能用于最终报告和误差观察，不能反向调规则。

成本没有配置可复核的价格快照，因此正式口径是 **N/A**，不能声称成本达标。历史 `269 passed, 1 warning` 是 2026-08-24 的归档离线结果，不自动等于本轮重新验证。

### 3.3 最近阶段硬门槛

最近阶段的首要安全门槛是 wrong-but-executed，业务正确率和一致性是并列准入门槛；三项任何一项失败都保持 `NOT READY`：

| 指标 | Chinook 门槛 | 当前 | 决策 |
|---|---:|---:|---|
| 业务正确率 | ≥ 80% | 79.00% | 未通过 |
| wrong-but-executed | ≤ 10% | 10.33% | 未通过，优先降低 |
| 重复一致性 | ≥ 85% | 83.00% | 未通过 |
| P95 Agent 时间 | ≤ 30 秒 | 22.91 秒 | 已通过，不是近期主矛盾 |
| Schema recall | ≥ profile 门槛 | 99.67% | 已通过 |
| Process trace coverage | ≥ 95% | 100% | 已通过，但不是 OTel |
| Token / 成本 | 先有完整采集和价格快照，再定数值门槛 | N/A | 不允许制造“通过” |

## 4. 正在进行的工作和安全续接点

| 工作 | 状态 | 证据与安全续接点 |
|---|---|---|
| v0.10.1 Query Plan/语义/粒度通用修复 | 已验证的历史实现，当前未提交 | 保留现有改动；从归档三轮失败账本继续，不重写 Prompt |
| Chinook 100 题双源准入 | 已验证但结果 NOT READY | 先自动化 failure ledger，再只看 development 设计修复 |
| AdventureWorks 24 题回归 | 已验证、窄范围 READY | 作为防跨源回归护栏，不外推 |
| Run/Event API、File Store、SSE、审批 | 实现中，历史离线测试通过 | 先保留单进程边界；PostgreSQL 作为 `AgentRunStore` 适配器迁移 |
| Context Engineering | 部分已有、增量计划 | 扩展现有 Context Enricher/Notice/Memory；先做清单与预算，不建新框架 |
| Result Validation | 确定性校验器、Run/Event 失败关闭和三轮正式评测已完成 | P95/一致性未过；保留实现，先处理延迟和波动，不进入 P0-D |
| OpenTelemetry | 仅计划 | 不替换 Prometheus/Audit/Evaluation；等 Run ID/Trace ID 契约稳定后接入 |
| PostgreSQL 多实例 Run/Event | 仅计划 | 需要迁移、真实 PostgreSQL 和多实例故障测试，必须单独获准 |
| Reviewer/多 Agent | 负结果，默认废弃 | v0.6 S4 比 S3 正确率低 8.33pp、P95 多 13.45 秒、成本高 47.61%，不重启默认路线 |
| MCP、Skill、新方言、Kubernetes | 条件计划/暂不适用 | 没有质量或权限收益证据前不实施 |

## 5. 附件要求与现有改造完整对照矩阵

| 附件能力/阶段 | 当前实现 | 真实验证 | 路线已含 | 未提交实现 | 关系类型 | 冲突/边界 | 推荐处理 | 前置、风险、验收证据 |
|---|---|---|---|---|---|---|---|---|
| 0 审计与基线 | 已有多轮日志，本轮完成只读审计 | 本轮只读 Git/代码/文档 | 是 | 否 | 完全相同 | 附件不能覆盖真实工作区 | 保留 | 本文、Git 状态；不改文件 |
| QUALITY-01 冻结基线与失败分类 | v0.10.1 已冻结双源并做三轮评测，失败清单多为手工报告 | 是，Chinook NOT READY | 是，核心主线 | 是 | 部分重合 | 若另建评测框架会重复；若用 holdout 调参会污染 | 合并；先自动化账本，再修机制 | 原 JSON、哈希、同配置聚合不变、split 防泄漏测试 |
| Context Engineering | Prompt、Runtime Notice、Context Enricher、Agent Memory、Schema/Contract 已有 | 局部有消融/评测 | v0.9+ 已含 | 是 | 部分重合 | 新 `ContextBuilder` 可能形成第二套顺序和权限真相 | 扩展现有抽象，补 manifest、预算、来源和敏感级别 | 先过 QUALITY 首轮；token 统计、截断/注入/缓存测试、同配置消融 |
| Schema/Agent Memory | 数据源隔离 Schema Memory 与 advisory Agent Memory 已有 | 是，范围有限 | 是 | 是 | 部分重合 | 记忆不能覆盖 RLS、合同或服务端用户范围 | 保留并加治理，不重建 | TTL/版本/来源/删除；跨租户、过期和提示注入测试 |
| Runtime Notice | middleware 尾部稳定注入已实现 | 历史测试与评测 | 是 | 是 | 完全相同 | 与新 Context 层重复时顺序可能冲突 | 保留为最高优先级运行时约束 | prompt 顺序快照、不可被 memory 覆盖测试 |
| Query Plan / Result Validation | Query Plan 与确定性 Result Validation 已贯通 Run/Event | 单元、真实 PG、三轮完整模型评测已完成 | 是 | 是 | 互补 | 展示别名曾造成误报，已用确定性括号/分隔符等价修复 | 保留实现；先处理 P95/一致性，再决定阶段退出 | 283 tests、真实 PG smoke、失败候选、三轮报告和 admission |
| RUN-01 PostgreSQL Run/Event | Store 接口、File/Memory、SSE、审批已实现；无 DB queue | 单进程历史离线验证 | v0.10 后续已含 | 是 | 互补 | 直接替换会破坏本地回退；单表轮询不自动等于可靠队列 | 在 `AgentRunStore` 后新增适配器，双写/迁移后切换 | 迁移审批、真实 PG、多实例 claim、lease/fencing、崩溃恢复、幂等证据 |
| OpenTelemetry Trace | Prometheus、自定义进程 Span、Audit、Evaluation trace 已有；OTel 无 | 现有三类各自已局部验证 | 后续计划 | 否 | 互补 | 四类证据目的不同，不能互相替代 | P1 接入，先定义 ID 和脱敏属性 | 新依赖需批准；W3C trace context、采样、导出故障、开销测试 |
| Prometheus / Audit / Evaluation Report | 已有 | 是，范围不同 | 是 | 是 | 完全相同 | 若统一进 OTel 会丢合规和评测语义 | 保留分工，用 run/eval/trace ID 关联 | 指标基数、审计不可改、报告可复现、脱敏检查 |
| Semantic Contract 与分析 Skill | Contract 1.0.1 是业务定义真相；Skill 未实现 | Contract 已双源验证 | v0.7+ | 是 | 部分重合/架构冲突 | Skill 再存指标公式会出现双真相 | Skill 只编排并引用 contract ID/version | manifest 一致性、合同缺失 fail-closed、无复制公式检查 |
| Tool Registry / RLS / SQL Governance 与 MCP | 权限链已有；MCP 无 | 权限/治理有测试 | 条件路线 | 是（现有链） | 互补/架构冲突 | MCP 若直连数据库会绕过服务端身份、RLS 和治理 | P2 只做现有 registry 的远程适配器 | OAuth audience/scope、禁止 token passthrough、跨租户和拒绝路径测试 |
| HITL 审批 | Run 级审批/澄清骨架已实现；风险分类与 SQL 前批准不完整 | 历史离线测试 | v0.10 后续 | 是 | 部分重合 | 不能只在外层批准后绕过 SQL 治理 | 合并到 Run + Governance 状态机 | 过期/重复审批、取消、重启、审计和 UI E2E |
| Reviewer / 多 Agent | 可选 reviewer/structured recovery 默认关 | v0.6 明确负结果 | 已有负实验结论 | 是 | 负结果证明不应实施 | 正确率、延迟、成本均变差 | 取消默认实施；只允许新假设的小规模隔离实验 | 必须预注册成功门槛，失败保留，不能进默认链 |
| Security / RLS 强化 | Tool Registry、注入、只读、RLS、治理已有 | 多为单元/局部集成 | 是 | 是 | 部分重合 | “有中间件”不等于端到端安全 | P1 补矩阵和故障测试 | 真实 PG、跨租户、scope、超时、注入、审计证据 |
| Performance / Cost | P95 已采；token/cost 部分缺失 | 延迟有，成本 N/A | 是 | 是 | 部分重合/指标冲突 | 没价格快照不能声称成本达标 | 先采集再定门槛；不以降质量换速度 | 同模型同配置、token/cost 完整率、预算超限 fail 明确 |
| 多数据源/新方言 | 两个 PostgreSQL 数据源，不是多方言 | 双源窄范围 | 产品范围明确限制 | 否 | 范围冲突 | 局部 Benchmark 不支持任意数据库结论 | 延后 | 新冻结集、方言治理、参考 SQL 和独立重复评测 |
| 通用 RAG / Kubernetes / 平台扩张 | 无必要闭环 | 无 | 非近期主线 | 否 | 暂不适用 | 会掩盖正确率问题并扩大运维面 | 取消近期工作 | 只有明确用户/容量证据后重开 |
| 文档、ADR、简历证据 | portfolio/ADR/报告较完整 | 有，但当前工作区未提交 | 是 | 是 | 部分重合 | 计划、历史证据和本轮结果容易混写 | 每阶段同步，保留 NOT READY 和负结果 | 能力—代码—测试—命令—报告一一对应 |

## 6. 最终阶段顺序

### P0：投递前必须完成

#### P0-A QUALITY-01A：失败账本自动化（当前阶段）

- 目标：从同配置重复报告自动输出每题稳定正确、稳定错误、波动，以及 development/test/holdout 分割指标。
- 明确不做：不改 Prompt、合同、数据、参考 SQL；不调用模型或数据库；不根据 holdout 写规则。
- 修改：扩展现有 `BenchmarkQualityAssessment`，补离线单元测试和阶段证据文档。
- 退出：原有八项聚合指标和 READY 决策不变；Chinook 复现 13 个稳定错误、17 个波动；输出分 split；测试通过。

#### P0-B QUALITY-01B：development 集通用修复

- 目标：按 result mismatch、query contract、SQL execution、semantic contract 分类，只修通用根因。
- 明确不做：不写 case ID 特判，不看 holdout 设计规则，不提高 LLM Judge 权重。
- 前置：P0-A 通过；如需真实模型费用、数据库或修改合同，先获用户批准。
- 退出：先冻结 development 失败用例；离线回归通过；同模型/快照/并发三轮中业务正确率 ≥80%、wrong-but-executed ≤10%、一致性 ≥85%，且 AdventureWorks 不回退。test 只作候选门，holdout 只在实现冻结后揭盲一次。

#### P0-C 真实 Result Validation 和事件语义

- 目标：以 Query Plan/Semantic Contract 为依据验证列、粒度、行数边界、空结果和数量类回答；修正 `result.validated`。
- 明确不做：不让 LLM Judge 充当运行时权限或唯一校验器。
- 退出：未校验不能发 validated；失败进入明确 recovery/failed 状态；单元、真实 PG、SSE/Run E2E 通过；准确率不回退。

#### P0-D 最小证据关联

- 目标：统一 `evaluation_case_id → run_id → event_id → trace_id`，但暂不安装 OTel。
- 退出：100% 评测样本能定位过程事件、SQL、合同版本、数据集哈希和最终判定；敏感内容脱敏。

### P1：近期工程化

#### P1-A Context 预算与清单

- 在现有 Context Enricher/Runtime Notice/Memory 上增加来源、版本、优先级、敏感级别、token 预算和截断原因。
- 通过稳定 Prompt 快照、注入、超预算、缓存失效和同配置消融；没有质量收益则回退，不为“用了 Context Engineering”保留代码。

#### P1-B PostgreSQL 多实例 Run/Event

- 新增 PostgreSQL `AgentRunStore`，保留 File Store 为本地回退；迁移前备份和版本校验。
- 数据模型至少包含 `agent_runs`、`agent_run_events`、`agent_run_approvals`、`agent_run_feedback`、`idempotency_keys`，并记录 tenant/user/database scope、版本、lease owner/expiry、attempt、fencing token。
- claim 必须在事务中原子完成。PostgreSQL 18 官方文档说明 `SKIP LOCKED` 适合多个消费者避免队列式表的锁争用，但会给出不一致视图，因此只能用于 claim 队列，不能用于业务结果读取。
- 退出：真实 PostgreSQL 迁移、双实例不重复执行、worker 崩溃接管、过期 lease、旧 fencing token 拒绝、取消/审批竞争、SSE 重连、幂等和回退恢复全部通过。

#### P1-C OpenTelemetry

- 保留 Prometheus（聚合指标）、Audit（合规事实）、Evaluation Report（质量证据），OTel 只负责分布式因果链和耗时诊断。
- 参考 2026-08-26 查阅的 OpenTelemetry Python 官方文档：应用发遥测需要 SDK，嵌套 span 表达父子操作，默认传播 W3C Trace Context/Baggage；新增依赖前必须请用户批准。
- 退出：API、Agent、Tool、SQL、Run worker 跨进程 trace 连通；属性低基数且脱敏；exporter 故障不阻断主流程；采样和性能开销有报告。

#### P1-D Memory/HITL/Security/性能

- Memory 增加 TTL、来源、版本、删除和跨租户隔离；HITL 与 Governance 共用状态机；补真实 PostgreSQL 安全和故障矩阵。
- Token/成本只有在采集完整率和价格快照明确后才设门槛；缺失继续写 N/A。

### P2：有条件才实施

- 分析 Skill：只编排 QueryMind 现有工具并引用 Semantic Contract，不复制业务定义。
- MCP：只暴露 Tool Registry 的受控能力；参考当前 MCP 授权规范，远程受保护服务需要按 OAuth 2.1 验证 token、audience 和 scope，禁止 token passthrough。未完成跨租户/权限证据前不启用。
- 新方言、多数据库、Kubernetes、通用 RAG：需要独立用户需求、冻结集和容量证据。
- Reviewer/多 Agent：保持默认关闭；没有能推翻 v0.6 负结果的新假设，不重开。

## 7. 逐文件修改和迁移清单

### 当前阶段 P0-A

| 文件 | 最小改动 |
|---|---|
| `src/QueryMind/core/evaluation/benchmark_admission.py` | 增加每题稳定性和 split 聚合模型/计算/Markdown 输出；保留现有 metrics/gates 字段 |
| `tests/test_evaluation_enterprise_metrics.py` | 先补稳定错误、波动、split、重复输入和旧指标不变测试 |
| `docs/portfolio/v0.10.2-quality-failure-ledger.md` | 记录命令、输入报告、真实输出、限制和下一续接点；文件存在则用带日期的新名 |

### 后续候选文件（不是本轮授权）

| 阶段 | 候选文件/目录 | 说明 |
|---|---|---|
| P0-B | `query_plan.py`、`semantic_contract.py`、`sql_semantics.py`、对应 tests | 只由 development 根因决定，不预设关键词修复 |
| P0-C | Result validator 新模块、`run_sql.py`、`agent.py`、`agent_run_executor.py`、event schema/tests | validator 复用 Plan/Contract；事件迁移保持兼容 |
| P1-A | 现有 context enhancer/middleware/memory/config/tests | 增量增加 manifest 和预算 |
| P1-B | `agent_run_store.py`、新 PostgreSQL store、migration、executor/API/tests | 新依赖/数据库/迁移需另行批准 |
| P1-C | observability 初始化、Agent/Tool/Run 埋点、config/tests | 新 OTel 包需另行批准 |
| P2 | Tool Registry 的 Skill/MCP adapter 和权限测试 | 不允许直连绕过治理 |

PostgreSQL Run/Event 迁移采用 `expand → backfill/验证 → 可选双写 → 读切换 → 观察 → contract`；任何 drop、覆盖或不可逆清理都不在默认授权内。回退时切回 File Store/旧读路径，保留新表和事件，不删除证据。

## 8. 冻结集和评测使用规则

1. development（001–060）允许做根因分析、规则设计和快速回归。
2. test（061–080）只用于候选版本门禁，不根据单题结果继续打补丁；若继续调优，必须回到 development 并新开候选版本。
3. holdout（081–100）仅在实现与阈值冻结后揭盲；揭盲后不得用于该版本调优。当前已有历史结果可用于诚实报告，但不能转化成 case 特判。
4. AdventureWorks 24 题只检查跨源回归，不能证明任意 PostgreSQL 或生产能力。
5. 同配置比较必须固定 dataset hash、参考 SQL、数据/模式快照、contract version、model、temperature、并发、strategy、judge 和代码快照；任何一项不同都报告 comparability issue。
6. 禁止修改参考答案、降低门槛、忽略失败、提高 LLM Judge 权重或只挑最好一轮。

## 9. 测试和故障矩阵

| 层级 | 必测内容 | 何时执行 |
|---|---|---|
| 单元 | stability/split 聚合、Plan、Contract、Validator、RLS、状态机、脱敏 | 每个阶段 |
| 冻结数据静态审计 | hash、ID 唯一、split/difficulty、schema/SQL 标识符、参考 SQL 成功率 | 每次数据版本变化；本轮只读 |
| 真实 PostgreSQL | 只读 SQL、事务、RLS、迁移、claim/lease/fencing、崩溃恢复 | 需用户批准环境/数据库后 |
| 多实例 | 两 worker 原子 claim、重复请求、接管、取消/审批竞争、SSE 重连 | P1-B |
| 真实模型 | 固定模型三轮、S0/S3/S5 对照、目标集后完整集 | 涉及费用，必须先批准 |
| E2E | 问题→Context→Plan→Governance→SQL→Validation→Recovery→Run/Event/UI | P0-C/P1-B |
| 安全 | 跨租户、越 scope、提示/SQL 注入、token 泄漏、MCP audience、审计脱敏 | 对应阶段 |
| 性能/成本 | P50/P95、token、模型/数据库耗时、队列等待、exporter 开销、价格快照 | 基线完整后 |
| 故障 | 模型/DB/存储/OTel 超时，进程崩溃，部分写，重复事件，磁盘满 | Run/OTel 阶段 |

## 10. NOT READY、回退和依赖风险

以下任一情况都必须保持 `NOT READY`：业务正确率、wrong-but-executed 或一致性未过；报告不可比较；参考 SQL/快照/hash 不一致；测试/holdout 被用于定向补丁；只用局部 Benchmark 外推生产；把回答完成误报为结果验证；成本缺失却声称成本达标。

每阶段使用 feature flag 或适配器边界回退，保留旧路径和原始报告。Context 可关闭新 manifest/压缩；validator 可先 shadow 只记录不拦截；PostgreSQL store 可切回 File Store；OTel exporter 可关闭；Skill/MCP 默认关闭。回退不删除事件、审计或失败证据。

主要依赖风险：真实模型费用和限额、真实 PostgreSQL/多实例环境、OTel 新包和 collector、MCP 授权实现、价格表时效。缺少这些条件时停止并请求批准，不静默换备用方案。

## 11. 文档同步清单

每阶段通过后才更新：

- `README.md` / `README_zh.md`：只更新已验证能力和运行命令。
- `CHANGELOG_PORTFOLIO.md`：记录版本、范围、负结果和 NOT READY。
- `docs/portfolio/`：阶段目标、代码、命令、结果、风险和续接点。
- `docs/adr/`：只有 Run Store、事件语义、OTel/MCP 权限等稳定架构决定才新增 ADR，不改写历史 ADR。
- 评测/状态/运行手册：记录 dataset hash、snapshot、model、成本完整性、三轮结果和 comparability issues。

## 12. 能力—代码—测试—命令—报告证据矩阵

| 能力 | 当前代码 | 测试/命令 | 报告证据 | 状态 |
|---|---|---|---|---|
| Semantic Contract | `core/semantic_contract/`、Tool middleware | contract/query-plan tests；真实模型双源评测 | v0.7、v0.10.1 双源报告 | 已验证，范围受冻结集限制 |
| SQL Governance/RLS | Tool Registry、RLS/Governance middleware | governance/RLS tests；真实 PG E2E 待强化 | ownership、v0.9/v0.10 | 已实现，生产安全证据不足 |
| Benchmark Admission | `benchmark_admission.py`、profiles | `pytest tests/test_benchmark_admission.py tests/test_evaluation_enterprise_metrics.py` | 双源 admission/final report | 已验证；Chinook NOT READY |
| Run/Event/SSE | Run schema/store/API/executor | `pytest tests/test_agent_run_runtime.py` 等 | v0.10 文档/执行记录 | 单进程实现中，非多实例 |
| Result Validation | `result_validation.py`、`run_sql.py`、Run executor | 283 tests；真实 PG smoke；三轮 100 题完整报告 | `v0.10.2-result-validation-runtime.md` | 工程已完成；P95/一致性待续接 |
| OpenTelemetry | 无正式 SDK 接入 | 无 | 无 | 仅计划 |
| Skill/MCP | 无受控 adapter | 无 | 无 | 条件 P2 |

每次交付要写出完整命令和真实数字。命令没有执行时必须写“未执行”和原因，历史输出不能冒充本轮测试。

## 13. 简历可用事实与禁用说法

### 当前可以使用，但必须带范围

- 建立了双 PostgreSQL 数据源的冻结 Text2SQL 评测和准入流程；Chinook 100 题、AdventureWorks 24 题，固定参考 SQL、快照、配置并做三轮重复评测。
- 在 v0.10.1 历史正式配置下，AdventureWorks 24 题业务正确率均值 86.11%，wrong-but-executed 6.94%；Chinook 100 题均值 79.00%，并诚实标记为 NOT READY。
- 实现/扩展了 Semantic Contract、Query Plan、Schema evidence、RLS/SQL Governance 和过程事件证据；具体“已验证”范围以报告为准。
- 做过 Reviewer 负实验并保留结论：相对单 Agent 准确率下降、延迟和成本上升，因此默认关闭。

### 绝对不能提前声称

- “生产就绪”“适配任意数据库”“支持多方言”或“企业级准确率”。
- “结果已验证”，如果只是 SQL 执行或回答完成。
- “多实例可靠队列”“Exactly-once”“分布式事务”，如果 PostgreSQL lease/fencing/故障测试未完成。
- “完整 OpenTelemetry 可观测”“MCP 安全接入”“Skill 体系完成”，在代码和安全证据落地前。
- “成本降低/达标”，当前价格和成本为 N/A。
- 用 AdventureWorks 24 题 READY 掩盖 Chinook NOT READY，或把局部/定向结果相加成总体准确率。

## 14. 方案自检

- 冻结数据、参考 SQL、快照和 hash 受保护；test/holdout 用法明确。
- v0.6 Reviewer 负结果保留，Reviewer/多 Agent 不进入默认路线。
- Context、Run Store、Semantic Contract、Tool Registry 和 Evaluation 都扩展现有抽象，不复制第二套。
- P0 先解决 wrong-but-executed、业务正确率、一致性和结果验证，没有用 MCP、Skill、OTel、Kubernetes 或新方言掩盖正确率。
- 所有 READY 都带 profile 和数据边界；Chinook 继续明确为 NOT READY。
- 安装依赖、真实模型费用、数据库迁移、多实例/外部系统、冻结数据变更和破坏性动作都设置了用户批准点。

## 15. 官方设计边界参考（2026-08-26 核对）

- PostgreSQL 18 `SELECT`：`https://www.postgresql.org/docs/current/sql-select.html`
- OpenTelemetry Python 手工埋点：`https://opentelemetry.io/docs/languages/python/instrumentation/`
- MCP Authorization（当前规范页面）：`https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization`

这些资料只约束后续设计，不代表相关依赖或能力已安装、实现或验证。进入对应阶段时必须再次核对项目锁定版本和最新官方文档。

## 16. 执行更新（2026-08-27）

P0-A 已通过。P0-B 在连接计数去重之后继续保留单表 `COUNT(*)` 误拦截修复；100 题
Chinook S5 三轮为 86% / 84% / 84%，业务正确率 84.67%、wrong-but-executed 9%
均已过官方 profile 门槛，但最差 P95 31.74 秒和一致性 83% 未过，自动 admission
仍为 `NOT READY`。随后计划聚合规则的局部 3/3 成功在完整集上退化为业务 82.33%、
wrong 11.67%，相关代码已撤回，负结果和报告保留。

因此仍停在 P0-B，未进入 P0-C/P0-D，也未把历史 AdventureWorks 结果冒充本轮回归。
当前保留能力、完整成本、负实验和下一安全续接点见
[`v0.10.2-quality-count-star-and-plan-negative-result.md`](v0.10.2-quality-count-star-and-plan-negative-result.md)。

## 17. P0-C 执行更新（2026-08-28，覆盖第 16 节的续接状态）

用户明确同意进入下一阶段后，P0-C 已完成确定性 Result Validation、`run_sql` 元数据、
Run/Event `result.validated` 语义修正和失败关闭。完整 Python 回归为 `283 passed,
1 warning`，Ruff、diff 检查和真实 Chinook 只读 PostgreSQL smoke 均通过。

失败候选没有删除：严格列名候选为业务 80% / wrong 12%；分隔符归一化候选为业务
83% / wrong 11%，两者暴露的展示别名误报均已用通用规则修复。最终候选三个完整
100 题运行分别为业务 84% / 82% / 84%、wrong 9% / 12% / 8%，三轮均值
83.33% / 9.67%，一致性 84%，最差 P95 76.01 秒。自动 admission 可比性问题为 0；
业务准确率和 wrong-but-executed 通过，P95 与一致性未通过，结果为 `NOT READY`。
被 DeepSeek 连接错误污染的 73/100 运行保持隔离，没有混入正式聚合。

P0-C 全部可计量模型成本为 `$0.799147`，与 P0-B 合计 `$2.102074`，仍低于 `$5`
上限。唯一安全续接点是先处理延迟和波动的通用根因，不进入 P0-D。完整证据见
[`v0.10.2-result-validation-runtime.md`](v0.10.2-result-validation-runtime.md)。
