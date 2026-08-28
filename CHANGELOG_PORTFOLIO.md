# Project Changelog

This file tracks changes maintained in `luyan9513/querymind-data-agent` on top of the upstream QueryMind project.

## Unreleased v0.10

### Added

- A bounded Agent-run lifecycle model with eight states, explicit transition rules, optimistic versions, terminal timestamps, and ordered redacted events.
- An `AgentRunStore` boundary with async-safe memory tests and an atomic JSON development adapter that survives process restart without storing raw idempotency keys.
- Versioned create/get/events/cancel APIs under `/api/querymind/v1/agent-runs`, including mandatory idempotency keys, request-conflict detection, tenant/user scoping, incremental event cursors, and idempotent cancellation.
- A process-local background executor that drives the existing Chat Agent, recovers queued Runs, fails closed on interrupted running work, and cooperates with cancellation.
- Live resumable SSE events, a shared trace ID, centrally redacted model/tool/Agent events, and process traces in evaluation artifacts.
- Persisted approval, rejection, clarification, and feedback flows with optimistic versions and tenant/user authorization; each human decision and state transition is atomic, and concurrent feedback uses append semantics.
- Stable runtime fault categories plus timeout fault injection, concurrent idempotency, and approval/cancellation race tests.
- A frozen 100-case Chinook benchmark with 60/20/20 splits, read-only reference validation, repeated-run quality gates, and a quality-assessment CLI.
- Comparable-run admission that fails closed when model, dataset, database snapshot, Schema snapshot, or another controlled setting is missing or differs.
- Cycle-safe dataset includes and dataset-scoped advisory SQL-shape checks, while business columns, filters, projections, and forbidden operations remain blocking.
- The normal local launcher mounts the file Agent-run store through `QUERYMIND_AGENT_RUNS_DIR`; existing chat endpoints remain compatible.
- Schema-derived `grain_keys`, conservative joined-entity distinct counts, explicit `partition_limit` checks, unplanned business-filter rejection, and actionable Query Plan repair hints.
- Chinook semantic catalog 1.0.1 adds a playlist bridge-table metric and source-owned support-representative relationship notes.
- Evaluation now selects the last successfully executed SQL before falling back to a rejected attempt.
- Deterministic runtime Result Validation checks accepted-plan output shape, alias-equivalent column order, row limits, scalar aggregation, observable grain, empty results, and required Semantic Contract status without persisting result values.
- The Run executor emits `result.validated` only for a passed latest successful SQL result; missing, failed, or inconclusive evidence emits `result.validation_failed` and fails the Run closed with stable error codes.

### Documented

- A focused product definition for governed sales and operations analytics, with explicit users, supported tasks, non-goals, offline admission metrics, and future online KPIs.
- A durable single-Agent runtime design covering Run/Step/Event state, versioned APIs, idempotency, cancellation, trace/redaction, bounded recovery, clarification, and risk-based human approval.
- ADR-0001 records why the project keeps a governed single-Agent pattern instead of adopting multi-Agent orchestration without measured benefit.
- A source-audited readiness gap report distinguishes existing Agent/evaluation strengths from missing production runtime, observability, HITL, and concurrency evidence.

### Limits

- The executor and atomic file adapter are single-process development components, not a distributed queue or multi-instance transactional store.
- Result Validation proves observable structure, not business-value correctness; the final P0-C candidate passes accuracy/error gates but still misses P95 and repeat-consistency gates.
- Interrupted `running` work fails closed because exact post-SQL replay is not yet safe; only queued work is resubmitted at startup.
- Trace stages are persisted as ordered events rather than normalized Step/Tool database tables; production authentication, leases, rate limits, and SLOs remain out of scope.
- A deployer must provide a risk policy to trigger approvals; absolute security blocks cannot be overridden.
- The 3-case smoke only validates the repaired path and is not used as the release accuracy number.

### Verified

- 20 concurrent creates with one idempotency key produce one Run; approval/cancel races allow one winner; cross-user decisions return 404.
- Timeout injection terminates as `provider_transient`; live SSE closes with `[DONE]`; persisted events redact credentials and row values.
- Chinook coverage has no profile deficit and all 100 reference SQL statements execute successfully in read-only validation.
- Corrected 3-case real-model smoke: 100% business correctness, 100% Agent SQL execution, 0% wrong-executed, and 100% process-trace coverage. This is not the release accuracy number.
- Three comparable 100-case real-model runs: 65%/69%/69% business accuracy, 67.67% mean business accuracy, 19.00% mean wrong-executed rate, 81.00% per-case consistency, and 18.846-second maximum P95 latency.
- The automated admission result is `NOT READY`: reference SQL, Schema Recall, answer coverage, latency, and Trace coverage passed; business accuracy, wrong-executed rate, and repeat consistency failed.
- Formal Python scope: `245 passed, 1 warning`; focused Ruff and `git diff --check` passed.
- v0.10.1 dual-source iteration: `269 passed, 1 warning`; contract audits and reference SQL pass Chinook 100/100 and AdventureWorks 24/24. Chinook S5 repeated admission averages 79.00% business accuracy and remains `NOT READY`; AdventureWorks averages 86.11% with 6.94% wrong-but-executed and passes the predefined 24-case cross-source regression gate. The interrupted HTTP 402 run was isolated and replaced by a fresh run from case zero.
- v0.10.2 retained quality snapshot: joined-count DISTINCT preservation and the single-table `COUNT(*)` false-block fix pass `272` formal Python tests. Three comparable Chinook S5 runs average 84.67% business accuracy and 9.00% wrong-but-executed; maximum P95 is 31.74 seconds and consistency is 83.00%, so admission remains `NOT READY`. A targeted 3/3 plan-aggregation experiment regressed the full benchmark to 82.33% business accuracy and 11.67% wrong-but-executed, so its code was reverted while the negative reports were retained. All measurable P0-B model runs cost $1.302927 under the approved $5 cap.
- P0-C Result Validation passes `283` formal Python tests, focused Ruff, whitespace checks, and a read-only Chinook PostgreSQL smoke. Three complete final-candidate 100-case runs reached 84%/82%/84% business accuracy and 9%/12%/8% wrong-but-executed; means are 83.33% and 9.67%. Accuracy/error gates passed, while 76.01-second maximum P95 and 84% consistency did not. A separate provider-error run was isolated and is not aggregated. P0-C measurable model cost is $0.799147, bringing P0-B plus P0-C to $2.102074 under the approved $5 cap.

## Unreleased v0.9 (development)

### Added

- A machine-readable 100-case benchmark admission profile with difficulty, category, business-domain, SQL-tag, repeat, accuracy, coverage, error-rate, and latency gates.
- A read-only coverage checker that does not connect to a database or model.
- A reference-SQL validator that constructs only the configured SQL runner and does not initialize an LLM, Neo4j, or Schema Memory.
- Twenty-six new Chinook cases across two reviewed batches, covering filtering, time series, year-over-year comparison, subqueries, per-group Top-N windows, null handling, CASE tiers, UNION, empty-dimension preservation, and cross-group comparison.
- Alternative SQL-contract feature groups so semantically valid WHERE/HAVING implementations can satisfy the same dataset contract.

### Verified

- The development dataset now contains 50/100 planned cases; all 50 reference SQL statements execute read-only and return non-empty results.
- The admission checker reports the remaining 50-case and per-stratum deficits instead of treating the partial dataset as production-ready; the filtering category has reached its minimum target.
- Formal Python scope: `222 passed, 1 warning`; `git diff --check` passed.

### Limits

- No 50-case real-model accuracy is claimed yet. The latest Agent evidence remains the frozen 24-case v0.8 r6 comparison.
- The benchmark is not frozen and has not reached 100 cases or three repeats per case.

## Unreleased v0.8

### Added

- Database-scoped Schema Memory identities and filters across Mem0 vector search, Neo4j graph traversal, schema hydration, and RRF fusion.
- An explicit fail-closed Neo4j migration for replacing the legacy `schema + table` constraint after preflight checks and field backfill.
- Official Chinook 1.4.5 PostgreSQL evaluation data with upstream MIT license and recorded SHA-256.
- A Chinook 1.0.0 semantic catalog with 12 approved metrics and a separate 24-case Chinese business benchmark.
- Regression tests for active-database propagation, same-name table isolation, composite identity, and migration ordering.
- Repeatable `--case-id` evaluation subsets and semantic-contract validation across CTEs and wrapped metric expressions.

### Verified

- Chinook local snapshot: 11 tables, 64 columns, 11 foreign keys, and 15,607 rows; the existing `querymind` role has SELECT on every public table.
- All 24 Chinook reference SQL statements execute in read-only transactions, return non-empty results, and pass their declared SQL contracts.
- Neo4j pre-migration dump, explicit compound-identity migration, and Chinook Schema Memory initialization completed with 0 cross-source relationships.
- Fair r6 24-case S0/S3/S5 comparison: business accuracy 66.67%/70.83%/83.33%, wrong-executed 33.33%/25.00%/12.50%, P95 1.74/17.51/14.39 seconds.
- Generic fixes for aggregate HAVING plan filters, CTE SELECT-scope grain checks, wrapped semantic expressions, equivalent numeric result types, and dataset-owned date-granularity comparison.
- Formal Python scope: `213 passed, 1 warning`; `git diff --check` passed.

### Limits

- Chinook passes the predefined 24-case development gates. This does not constitute production admission: only one r6 run exists, the Wilson interval remains wide, and the benchmark must expand to at least 100 stratified questions with repeated runs.
- A post-run wrapped-expression false-block fix passed local tests, but its full real-model rerun was stopped by model-provider insufficient balance and is not included in the result.
- The 24-case development benchmark is not a production guarantee; formal source admission should expand to at least 100 stratified questions and repeated runs.

## Unreleased v0.7

### Added

- Versioned data-source semantic-contract catalogs with owner, status, metric formula, tables, fields, grain, time field, filters, and aliases.
- Contract candidates in Schema Retrieve, version-bound citations in Query Plan, and SQL AST validation before execution.
- S5 isolated evaluation mode, semantic coverage/pass/rejection metrics, and S0-S5 comparison support.
- An AdventureWorks 1.0.0 catalog with 14 approved reusable metrics and a database-neutral template.

### Changed

- Overall strict/business accuracy now uses every dataset case as the denominator; abstentions are not silently removed.
- Output aliases are advisory, while metric tables, fields, formulas, and required filters remain enforceable.
- Contract matching returns candidates; uncovered metrics continue through existing governance rather than failing solely because a catalog is incomplete.

### Verified

- `192 passed, 1 warning`; focused Ruff and whitespace checks passed.
- Final real-model smoke run passed 3/3.
- Final 24-case S5: 58.33% strict, 66.67% business, 87.50% coverage, 76.19% executed-answer business precision, 20.83% wrong executed, P95 33.92 seconds.
- S0-S5 comparison checker reported `comparable=true`.

### Limits

- Results cover one AdventureWorks run and are not a cross-database or production guarantee.
- Five of 24 questions still executed incorrect results; metric contracts do not yet fully constrain dimensions, joins, ordering, output grain, or conditional aggregate variants.
- Normal chat keeps semantic contracts disabled until a data source is explicitly configured and admitted.

## Unreleased v0.4

### Added

- Isolated `s0`, `s1`, and `s2` evaluation modes for single-shot, Agent without Query Plan, and Agent with Query Plan.
- A real no-Agent baseline with one fixed-budget schema retrieval, one LLM generation, and one governed SQL attempt.
- Mode-safe checkpoints and a fairness-checked S0/S1/S2 comparison exporter.
- Wilson 95% accuracy intervals, P50/P95/max latency, tool totals, wrong-executed rate, Recovery Yield, False Block Rate, and Plan Acceptance Precision.

### Verified

- 167 Python tests pass with one inherited Pydantic deprecation warning.
- Evaluation and comparison CLI help paths load without model or database calls.

### Limits

- Completed the first fair real-model comparison: 24 frozen questions for each of S0/S1/S2, 72 samples in total.
- Observed strict/business accuracy of 20.83%/25.00% (S0), 33.33%/41.67% (S1), and 50.00%/54.17% (S2).
- S2 reduced wrong-but-executed answers to 41.67%, but its P95 latency reached 53.13 seconds; the result is not a production or cross-dataset accuracy guarantee.
- Recorded per-question SQL, deterministic correctness, failure reasons, Wilson intervals, tool usage and cost in the generated detailed and comparison reports.
- Current end-to-end comparison keeps the Schema Memory snapshot and retrieval budget fixed, but lets Agent modes choose retrieval wording and additional retrievals. A shared-evidence causal test remains pending.

## Unreleased v0.3

### Added

- Schema-grounded `submit_query_plan` tool and turn-local table/column/primary-key evidence.
- Runtime plan gate before `run_sql`, including SQL-to-plan alignment checks.
- Bounded query-plan recovery that exposes only schema retrieval and plan submission.
- Direct physical-table lookup and a three-case recovery smoke dataset.
- Agent-side SQL execution success as a separate evaluation metric.

### Changed

- Required-field graph retrieval now normalizes qualified names, prefers exact matches, and ranks by field coverage.
- Graph-only retrieval hydrates complete table schemas; direct lookup exposes complete fields.
- Calendar-bucket and base-row counting guidance is explicit; unsupported single-table primary-key `COUNT(DISTINCT)` plans are rejected.
- Strict/business/first-SQL correctness now require the Agent trace to show a successful SQL execution rather than relying only on evaluator re-execution.

### Verified

- 157 Python tests pass with one inherited Pydantic deprecation warning.
- Final three-case DeepSeek v4 Pro smoke run: 100% strict/business correctness, SQL Contract, Schema Recall, and Agent SQL execution; 66.67% first-SQL correctness.

### Limits

- The three-case smoke result is not a 24-case release benchmark or a cross-database guarantee.

## v0.1.0-portfolio - 2026-07-14

### Added

- DeepSeek and SiliconFlow provider configuration with component-level key fallback.
- `BAAI/bge-m3` fixed-dimension compatibility for Mem0 and pgvector.
- Read-only PostgreSQL PK/FK extraction through `pg_catalog`.
- Composite and cross-schema foreign-key modeling for Neo4j.
- Persistent LLM-generated conversation titles and automatic history refresh.
- Workflow message persistence, legacy conversation fallback, and mobile workspace fixes.
- Provider, graph relationship, conversation-title, and chat-history tests.
- Playwright as a reproducible browser-verification dependency.
- Clean-clone frontend builds now create the generated static directory before syncing the bundle.

### Verified

- 117 Python tests pass.
- AdventureWorks initialization covers 68 tables and 456 fields.
- Schema Memory contains 68 vectors, 91 `FK_TO` relationships, and 91 field-level `REFERENCES` relationships.

### Known Limitations

- No production deployment or concurrency benchmark.
- No standalone Text2SQL accuracy benchmark for this fork yet.
- Cloud-model retry, circuit breaking, and alerting are not implemented.
