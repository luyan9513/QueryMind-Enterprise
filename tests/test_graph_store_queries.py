from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.integrations.schemamemory.graph_layer.graph_store import (  # noqa: E402
    Neo4jGraphStore,
    _build_relationship_pattern,
    _resolve_relationship_table,
)


def test_table_uniqueness_constraint_includes_database_name() -> None:
    constraint = " ".join(Neo4jGraphStore.CREATE_CONSTRAINTS)

    assert "database_name" in constraint
    assert "schema_name" in constraint
    assert "table_name" in constraint


class _RecordResult:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    def single(self):
        return {"count": self._count}

    def consume(self) -> None:
        return None


class _MigrationTransaction:
    def __init__(self, *, missing_tables: int = 0, duplicate_tables: int = 0) -> None:
        self.missing_tables = missing_tables
        self.duplicate_tables = duplicate_tables

    def run(self, query: str):
        if "SET f.database_name" in query:
            return _RecordResult(12)
        if "occurrences > 1" in query:
            return _RecordResult(self.duplicate_tables)
        if "MATCH (t:Table)" in query and "database_name" in query:
            return _RecordResult(self.missing_tables)
        return _RecordResult(12)


class _MigrationSession:
    def __init__(self, tx: _MigrationTransaction, commands: list[str]) -> None:
        self.tx = tx
        self.commands = commands

    def execute_read(self, callback):
        return callback(self.tx)

    def execute_write(self, callback):
        return callback(self.tx)

    def run(self, query: str):
        self.commands.append(query)
        return _RecordResult()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _MigrationDriver:
    def __init__(self, tx: _MigrationTransaction) -> None:
        self.tx = tx
        self.commands: list[str] = []

    def session(self):
        return _MigrationSession(self.tx, self.commands)


def _migration_store(tx: _MigrationTransaction) -> Neo4jGraphStore:
    store = object.__new__(Neo4jGraphStore)
    store._driver = _MigrationDriver(tx)
    return store


def test_database_scope_migration_stops_before_mutation_when_table_id_is_missing() -> None:
    store = _migration_store(_MigrationTransaction(missing_tables=1))

    try:
        asyncio.run(store.migrate_legacy_database_scope())
    except ValueError as exc:
        assert "missing database_name" in str(exc)
    else:
        raise AssertionError("migration should fail closed")

    assert store.driver.commands == []


def test_database_scope_migration_backfills_then_replaces_legacy_constraint() -> None:
    store = _migration_store(_MigrationTransaction())

    result = asyncio.run(store.migrate_legacy_database_scope())

    assert result["fields_backfilled"] == 12
    assert "database_name" in store.driver.commands[0]
    assert store.driver.commands[1] == "DROP CONSTRAINT table_name_unique IF EXISTS"


def test_delete_table_schema_is_database_scoped_and_fails_closed_when_ambiguous() -> None:
    calls: list[tuple[str, dict]] = []

    class _DeleteResult:
        def __iter__(self):
            return iter([])

    class _DeleteTransaction:
        def run(self, query: str, **params):
            calls.append((query, params))
            return _DeleteResult()

    class _DeleteSession:
        def execute_write(self, callback):
            return callback(_DeleteTransaction())

    class _DeleteDriver:
        def session(self):
            return _DeleteSession()

    store = object.__new__(Neo4jGraphStore)
    store._driver = _DeleteDriver()

    deleted = asyncio.run(
        store.delete_table_schema(
            "orders",
            schema_name="public",
            database_name="source_b",
        )
    )

    assert deleted is False
    assert calls[0][1]["database_name"] == "source_b"
    assert "WHERE size(tables) = 1" in calls[0][0]


def test_build_relationship_pattern_uses_valid_cypher_syntax() -> None:
    assert _build_relationship_pattern(None, 2) == ":FK_TO|REFERENCES*1..2"


def test_build_relationship_pattern_strips_colons_and_duplicates() -> None:
    assert _build_relationship_pattern(
        ["FK_TO", ":REFERENCES", "FK_TO"],
        2,
    ) == ":FK_TO|REFERENCES*1..2"


def test_build_relationship_pattern_can_restrict_to_single_type() -> None:
    assert _build_relationship_pattern(
        None,
        3,
        min_hops=0,
        default_types=("FK_TO",),
    ) == ":FK_TO*0..3"


def test_resolve_relationship_table_prefers_explicit_schema() -> None:
    assert _resolve_relationship_table("person", "person", "sales") == (
        "person",
        "person",
    )


def test_resolve_relationship_table_supports_legacy_dotted_name() -> None:
    assert _resolve_relationship_table("person.person", None, "sales") == (
        "person",
        "person",
    )


def test_resolve_relationship_table_falls_back_to_source_schema() -> None:
    assert _resolve_relationship_table("currency", None, "sales") == (
        "sales",
        "currency",
    )
