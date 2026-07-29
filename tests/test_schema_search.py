from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from QueryMind.integrations.schemamemory.schema_search import (  # noqa: E402
    HybridSearchConfig,
    SchemaSearch,
)
from QueryMind.integrations.schemamemory.vector_layer import (  # noqa: E402
    VectorSearchResult,
)
from QueryMind.integrations.schemamemory.memory import (  # noqa: E402
    Neo4jMem0SchemaMemory,
)
from QueryMind.core.evaluation.runtime import NoOpAgentMemory  # noqa: E402
from QueryMind.core.tool import ToolContext  # noqa: E402
from QueryMind.core.user import User  # noqa: E402


class _VectorStore:
    async def search_by_query(self, **kwargs):
        return [
            VectorSearchResult(
                table_name="orders",
                schema_name="sales",
                memory_id="orders-memory",
                score=0.92,
                metadata={},
            )
        ]


class _GraphStore:
    def __init__(self) -> None:
        self.calls = []

    async def find_related_tables(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "table": {
                    "table_name": "customers",
                    "schema_name": "sales",
                },
                "hops": 1,
            }
        ]


def test_hybrid_search_expands_vector_seeds_by_one_fk_hop() -> None:
    graph_store = _GraphStore()
    search = SchemaSearch(
        _VectorStore(),
        graph_store,
        HybridSearchConfig(graph_seed_count=1, graph_seed_max_hops=1),
    )

    results = asyncio.run(search.search_hybrid("customer order analysis", limit=10))

    assert [f"{item.schema_name}.{item.table_name}" for item in results] == [
        "sales.orders",
        "sales.customers",
    ]
    assert results[0].source == "vector"
    assert results[1].source == "graph"
    assert graph_store.calls == [
        {"table_name": "orders", "schema_name": "sales", "max_hops": 1}
    ]


def test_hybrid_search_tolerates_graph_expansion_failure() -> None:
    class _FailingGraphStore:
        async def find_related_tables(self, **kwargs):
            raise RuntimeError("graph unavailable")

    search = SchemaSearch(_VectorStore(), _FailingGraphStore())

    results = asyncio.run(search.search_hybrid("customer order analysis", limit=10))

    assert len(results) == 1
    assert results[0].table_name == "orders"
    assert results[0].source == "vector"


def test_required_fields_strip_qualifiers_and_prioritize_broad_coverage() -> None:
    class _FieldGraphStore:
        def __init__(self) -> None:
            self.calls = []

        async def find_tables_by_field(self, **kwargs):
            self.calls.append(kwargs)
            matches = {
                "orderdate": ["salesorderheader"],
                "totaldue": ["salesorderheader", "customer"],
            }
            return [
                {
                    "table": {
                        "table_name": table_name,
                        "schema_name": "sales",
                    },
                    "field": {"field_name": kwargs["field_name"]},
                }
                for table_name in matches.get(kwargs["field_name"].lower(), [])
            ]

    graph_store = _FieldGraphStore()
    search = SchemaSearch(_VectorStore(), graph_store)

    results = asyncio.run(
        search.search_hybrid(
            "monthly sales",
            required_fields=[
                "sales.salesorderheader.orderdate",
                "SalesOrderHeader.TotalDue",
            ],
            limit=1,
        )
    )

    assert results[0].table_name == "salesorderheader"
    assert graph_store.calls == [
        {"field_name": "orderdate", "exact_match": True, "limit": 50},
        {"field_name": "TotalDue", "exact_match": True, "limit": 50},
    ]


def test_field_search_falls_back_to_contains_only_when_exact_has_no_match() -> None:
    class _FallbackGraphStore:
        def __init__(self) -> None:
            self.calls = []

        async def find_tables_by_field(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["exact_match"]:
                return []
            return [
                {
                    "table": {
                        "table_name": "orders",
                        "schema_name": "sales",
                    },
                    "field": {"field_name": "order_total"},
                }
            ]

    graph_store = _FallbackGraphStore()
    search = SchemaSearch(_VectorStore(), graph_store)

    results = asyncio.run(
        search._search_graph_by_fields(["total"], limit=10)
    )

    assert results[0]["field_match_count"] == 1
    assert graph_store.calls == [
        {"field_name": "total", "exact_match": True, "limit": 10},
        {"field_name": "total", "exact_match": False, "limit": 10},
    ]


def test_graph_only_search_hydrates_the_complete_table_schema() -> None:
    class _HybridSearch:
        async def _search_graph_by_fields(self, **kwargs):
            return [
                {
                    "table": {
                        "database_name": "warehouse",
                        "schema_name": "sales",
                        "table_name": "orders",
                    },
                    "field": {"field_name": "orderdate"},
                }
            ]

    class _Neo4jStore:
        async def get_table_schema(self, **kwargs):
            return {
                "table": {
                    "database_name": "warehouse",
                    "schema_name": "sales",
                    "table_name": "orders",
                    "domain": "sales",
                    "description": "Customer orders",
                },
                "fields": [
                    {"field_name": "order_id", "data_type": "integer"},
                    {"field_name": "orderdate", "data_type": "date"},
                ],
            }

    memory = object.__new__(Neo4jMem0SchemaMemory)
    memory._hybrid_search = _HybridSearch()
    memory._neo4j_store = _Neo4jStore()
    context = ToolContext(
        user=User(
            id="u1",
            username="tester",
            email="tester@example.com",
            group_memberships=["user"],
        ),
        conversation_id="conv-graph",
        request_id="req-graph",
        agent_memory=NoOpAgentMemory(),
        metadata={},
    )

    results = asyncio.run(
        memory.search_schema(
            "orders",
            context,
            search_mode="graph_only",
            required_fields=["orderdate"],
        )
    )

    assert [
        field.field_name for field in results[0].table_schema.field_definitions
    ] == ["order_id", "orderdate"]
