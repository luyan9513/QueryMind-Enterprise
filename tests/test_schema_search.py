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
