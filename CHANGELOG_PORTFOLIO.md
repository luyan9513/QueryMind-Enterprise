# Portfolio Changelog

This file tracks changes maintained in `luyan9513/QueryMind-Enterprise` on top of the upstream QueryMind project.

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
