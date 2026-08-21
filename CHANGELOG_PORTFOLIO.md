# Project Changelog

This file tracks changes maintained in `luyan9513/querymind-data-agent` on top of the upstream QueryMind project.

## Unreleased v0.10 (development)

### Added

- A bounded Agent-run lifecycle model with eight states, explicit transition rules, optimistic versions, terminal timestamps, and ordered redacted events.
- An `AgentRunStore` boundary with async-safe memory tests and an atomic JSON development adapter that survives process restart without storing raw idempotency keys.
- Versioned create/get/events/cancel APIs under `/api/querymind/v1/agent-runs`, including mandatory idempotency keys, request-conflict detection, tenant/user scoping, incremental event cursors, and idempotent cancellation.
- The normal local launcher now mounts the file Agent-run store through `QUERYMIND_AGENT_RUNS_DIR`; existing chat endpoints and Text2SQL behavior are unchanged.

### Documented

- A focused product definition for governed sales and operations analytics, with explicit users, supported tasks, non-goals, offline admission metrics, and future online KPIs.
- A durable single-Agent runtime design covering Run/Step/Event state, versioned APIs, idempotency, cancellation, trace/redaction, bounded recovery, clarification, and risk-based human approval.
- ADR-0001 records why the project keeps a governed single-Agent pattern instead of adopting multi-Agent orchestration without measured benefit.
- A source-audited readiness gap report distinguishes existing Agent/evaluation strengths from missing production runtime, observability, HITL, and concurrency evidence.

### Limits

- v0.10-A1 only creates queued lifecycle records; no worker currently executes the existing Chat Agent from a Run.
- Event history supports cursor reads but not a live SSE wait loop. Step/Tool persistence, unified tracing, resumable approvals, feedback, and online KPI collection are not implemented.
- The atomic file adapter is single-process development storage, not a multi-instance transactional store.
- The v0.9 benchmark remains at 50/100 and has no 50-case real-model accuracy result; expansion resumes after the runtime event contract is stable.

### Verified

- Focused Agent-run/store/API/history scope: `10 passed, 1 warning`.
- Formal Python scope: `229 passed, 1 warning`; focused Ruff and `git diff --check` passed.

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
