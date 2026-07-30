"""
Schema retrieve tool for searching table schemas from SchemaMemory.

This tool allows the LLM to retrieve relevant table schemas based on
natural language queries, supporting multiple search modes (hybrid, vector,
graph, expand) determined by LLM agentic decision-making.
"""

import logging
import os
from typing import List, Optional, Type, Dict, Any

from pydantic import BaseModel, Field
from enum import Enum

from QueryMind.core.tool import Tool, ToolContext, ToolResult
from QueryMind.core.components import UiComponent
from QueryMind.components.rich.schema_retrieve import (
    SchemaRetrieveCardComponent,
    SchemaTableDisplay,
    SchemaFieldDisplay,
)
from QueryMind.components.simple import SimpleTextComponent
from QueryMind.capabilities.schema_memory import SchemaMemory
from QueryMind.capabilities.schema_memory.models import SchemaSearchResult
from QueryMind.core.agent.semantic_contract import (
    SemanticContractCatalog,
    format_semantic_contracts_for_llm,
)

logger = logging.getLogger(__name__)


class SearchMode(str, Enum):
    """Schema search mode enumeration.

    Search modes are determined by LLM agentic decision-making based on
    the query semantics and context.
    """

    VECTOR = "vector"  # Semantic similarity priority
    GRAPH = "graph"  # FK relationship exploration
    HYBRID = "hybrid"  # Balanced mode (default)
    EXPAND = "expand"  # Seed-based expansion
    DIRECT = "direct"  # Exact physical table lookup through table_names


class GraphHint(str, Enum):
    """Stable graph intent hint for schema retrieval."""

    NONE = "none"
    DOMAIN = "domain"
    FIELDS = "fields"
    EXPAND = "expand"


SCHEMA_MEMORY_SEARCH_MODE_MAP = {
    SearchMode.VECTOR: "vector_only",
    SearchMode.GRAPH: "graph_only",
    SearchMode.HYBRID: "hybrid",
    SearchMode.EXPAND: "graph_expand",
    SearchMode.DIRECT: "hybrid",
}


def _normalize_table_ref(table_ref: str) -> Optional[Dict[str, Any]]:
    """Normalize a table reference into schema/table/full_name parts."""
    cleaned = table_ref.strip().strip("`")
    if not cleaned:
        return None

    parts = [part for part in cleaned.split(".") if part]
    if len(parts) >= 3:
        database_name = ".".join(parts[:-2])
        schema_name = parts[-2]
        table_name = parts[-1]
    elif len(parts) == 2:
        database_name = None
        schema_name, table_name = parts
    elif len(parts) == 1:
        database_name = None
        schema_name = "public"
        table_name = parts[0]
    else:
        return None

    return {
        "database_name": database_name,
        "schema_name": schema_name,
        "table_name": table_name,
        "full_name": cleaned,
    }


def _normalize_field_list(fields: Optional[List[str]]) -> List[str]:
    """Normalize a list of field names while preserving order."""
    if not fields:
        return []

    normalized: List[str] = []
    seen = set()
    for field in fields:
        value = str(field).strip()
        if value and value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def _normalize_table_refs(table_refs: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Normalize a list of table references into structured refs."""
    if not table_refs:
        return []

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for table_ref in table_refs:
        parsed = _normalize_table_ref(str(table_ref))
        if not parsed:
            continue
        key = parsed["full_name"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(parsed)
    return normalized


class SchemaRetrieveToolArgs(BaseModel):
    """Arguments for schema retrieval tool.

    The search_mode parameter should be determined by LLM based on the
    rules in system prompt.
    """

    query: str = Field(
        default="",
        description="Natural language query for finding relevant tables, e.g., 'find tables related to customer analysis'"
    )

    search_mode: Optional[SearchMode] = Field(
        default=None,
        description="Search mode: hybrid (default), vector, graph, expand, or direct when table_names are known."
    )

    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of tables to return (default: 10)"
    )

    similarity_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Vector similarity threshold (default: 0.4)"
    )

    domain_filter: Optional[str] = Field(
        default=None,
        description="Business domain filter (optional)"
    )

    graph_hint: GraphHint = Field(
        default=GraphHint.NONE,
        description="Stable graph retrieval hint: none, domain, fields, or expand"
    )

    required_fields: List[str] = Field(
        default_factory=list,
        description="Required field names when graph_hint=fields"
    )

    table_names: List[str] = Field(
        default_factory=list,
        description=(
            "Exact physical table names already known from schema evidence or a "
            "rejected query plan, for example sales.orders"
        ),
    )

    seed_tables: List[str] = Field(
        default_factory=list,
        description="Seed table names for expand mode (system-injected when applicable)"
    )


class SchemaRetrieveTool(Tool[SchemaRetrieveToolArgs]):
    """Tool for retrieving relevant table schemas from SchemaMemory.

    This tool enables the LLM to search for database table schemas using
    natural language queries. The retrieval supports multiple modes:

    - hybrid: Balanced mode combining vector and graph search (default)
    - vector: Pure semantic similarity search
    - graph: FK relationship exploration
    - expand: Seed-based expansion from known tables

    The search mode is determined by the LLM based on query semantics
    and context availability (seed_tables). See system prompt for detailed rules.

    Example:
        >>> tool = SchemaRetrieveTool(schema_memory=memory)
        >>> result = await tool.execute(context, args)
    """

    def __init__(
        self,
        schema_memory: SchemaMemory,
        *,
        max_initial_results: Optional[int] = None,
        semantic_contract_catalog: SemanticContractCatalog | None = None,
    ):
        """Initialize the tool with a SchemaMemory instance.

        Args:
            schema_memory: SchemaMemory implementation for schema retrieval
        """
        self._schema_memory = schema_memory
        self._semantic_contract_catalog = semantic_contract_catalog
        configured_limit = max_initial_results
        if configured_limit is None:
            try:
                configured_limit = int(
                    os.getenv("SCHEMA_RETRIEVE_MAX_INITIAL_RESULTS", "12")
                )
            except ValueError:
                configured_limit = 12
        self._max_initial_results = max(1, min(50, configured_limit))

    @property
    def name(self) -> str:
        return "schema_retrieve"

    @property
    def description(self) -> str:
        return """
Search for table schemas based on business semantics. Supports multiple search modes:
- hybrid: Default balanced mode
- vector: Semantic similarity search
- graph: FK relationship exploration
- expand: Expand from seed tables (provided by system)
- direct: Exact lookup of the physical names in table_names
When exact physical table names are already known, pass table_names to fetch them directly.
"""

    @property
    def access_groups(self) -> List[str]:
        return []
    
    def get_args_schema(self) -> Type[SchemaRetrieveToolArgs]:
        return SchemaRetrieveToolArgs

    async def execute(
        self,
        context: ToolContext,
        args: SchemaRetrieveToolArgs
    ) -> ToolResult:
        """Execute schema retrieval.

        Args:
            context: Tool execution context
            args: Schema retrieval arguments

        Returns:
            ToolResult with schema information for LLM and UI
        """
        search_mode = args.search_mode or SearchMode.HYBRID
        effective_search_mode = search_mode
        required_fields = _normalize_field_list(args.required_fields)
        exact_table_refs = _normalize_table_refs(args.table_names)
        seed_table_refs = _normalize_table_refs(args.seed_tables)
        context_schema_retrieve = (
            context.metadata.get("schema_retrieve_context", {})
            if context.metadata
            else {}
        )
        context_seed_tables = context_schema_retrieve.get("seed_tables", [])
        context_seed_table_refs = context_schema_retrieve.get("seed_table_refs", [])
        contract_query = "\n".join(
            item
            for item in [context.raw_user_message or "", args.query or ""]
            if item.strip()
        )
        semantic_contracts = (
            self._semantic_contract_catalog.build_runtime_snapshot(contract_query)
            if self._semantic_contract_catalog is not None
            else {}
        )

        try:
            if not exact_table_refs and not args.query.strip():
                raise ValueError(
                    "query is required when table_names is not provided"
                )

            seed_table_refs.extend(_normalize_table_refs(context_seed_tables))
            if context_seed_table_refs:
                for ref in context_seed_table_refs:
                    if isinstance(ref, dict):
                        parsed = _normalize_table_ref(
                            ref.get("full_name")
                            or ".".join(
                                filter(
                                    None,
                                    [
                                        ref.get("database_name"),
                                        ref.get("schema_name"),
                                        ref.get("table_name"),
                                    ],
                                )
                            )
                        )
                    else:
                        parsed = _normalize_table_ref(str(ref))
                    if parsed:
                        seed_table_refs.append(parsed)

            # Deduplicate while preserving order.
            deduped_refs: List[Dict[str, Any]] = []
            seen_seed_tables = set()
            for ref in seed_table_refs:
                key = ref["full_name"]
                if key in seen_seed_tables:
                    continue
                seen_seed_tables.add(key)
                deduped_refs.append(ref)
            seed_table_refs = deduped_refs

            required_fields = _normalize_field_list(args.required_fields)

            if args.graph_hint == GraphHint.EXPAND or search_mode == SearchMode.EXPAND:
                effective_search_mode = SearchMode.EXPAND

            requested_limit = args.limit
            effective_limit = requested_limit
            if (
                not seed_table_refs
                and effective_search_mode in {SearchMode.HYBRID, SearchMode.VECTOR}
            ):
                effective_limit = min(requested_limit, self._max_initial_results)

            memory_search_mode = SCHEMA_MEMORY_SEARCH_MODE_MAP.get(
                effective_search_mode,
                "hybrid",
            )

            logger.info(
                f"Schema retrieval: query='{args.query}', mode={effective_search_mode.value}, "
                f"hint={args.graph_hint.value}, limit={effective_limit} "
                f"(requested={requested_limit})"
            )

            missing_exact_tables: List[str] = []
            if exact_table_refs:
                results = []
                for ref in exact_table_refs[:effective_limit]:
                    table_schema = await self._schema_memory.get_table_schema(
                        table_name=ref["table_name"],
                        schema_name=ref["schema_name"],
                        database_name=ref["database_name"],
                        context=context,
                    )
                    if table_schema is None:
                        missing_exact_tables.append(ref["full_name"])
                        continue
                    results.append(
                        SchemaSearchResult(
                            table_schema=table_schema,
                            similarity_score=1.0,
                            rank=len(results) + 1,
                            match_reason="Exact physical table lookup",
                        )
                    )
            else:
                # Perform semantic/graph schema search.
                results = await self._schema_memory.search_schema(
                    query=args.query,
                    context=context,
                    search_mode=memory_search_mode,
                    limit=effective_limit,
                    similarity_threshold=args.similarity_threshold,
                    domain_filter=args.domain_filter,
                    required_fields=required_fields or None,
                    seed_tables=[ref["full_name"] for ref in seed_table_refs]
                    if effective_search_mode == SearchMode.EXPAND
                    else None,
                )

            # Format results for LLM
            llm_content = self._format_result_for_llm(
                results=results,
                search_mode=effective_search_mode,
                query=args.query,
                required_fields=required_fields,
                include_all_fields=bool(exact_table_refs),
            )
            if missing_exact_tables:
                llm_content += (
                    "\nExact table names not found in Schema Memory: "
                    + ", ".join(missing_exact_tables)
                )
            contract_content = format_semantic_contracts_for_llm(
                semantic_contracts
            )
            if contract_content:
                llm_content += "\n\n" + contract_content

            # Build UI component
            ui_component = self._build_ui_component(
                results=results,
                query=args.query,
                search_mode=effective_search_mode,
            )

            selected_tables = [result.table_schema.full_name for result in results if result.table_schema]
            selected_table_refs = []
            selected_columns = {}
            selected_primary_keys = {}
            selected_column_refs = []
            for result in results:
                schema = result.table_schema
                if not schema:
                    continue
                selected_table_refs.append(
                    {
                        "full_name": schema.full_name,
                        "database_name": schema.database_name,
                        "schema_name": schema.schema_name,
                        "table_name": schema.table_name,
                    }
                )
                field_names = [
                    field.field_name
                    for field in schema.field_definitions
                    if field.field_name
                ]
                selected_columns[schema.full_name] = field_names
                selected_primary_keys[schema.full_name] = list(
                    schema.primary_key_fields
                )
                selected_column_refs.extend(
                    f"{schema.full_name}.{field_name}"
                    for field_name in field_names
                )

            # Build metadata
            metadata = {
                "tool_name": self.name,
                "search_mode": effective_search_mode.value,
                "graph_hint": args.graph_hint.value,
                "query": args.query,
                "total_results": len(results),
                "requested_limit": requested_limit,
                "effective_limit": effective_limit,
                "selected_tables": selected_tables,
                "selected_table_refs": selected_table_refs,
                "selected_columns": selected_columns,
                "selected_primary_keys": selected_primary_keys,
                "selected_column_refs": selected_column_refs,
                "domain_filter": args.domain_filter,
                "required_fields": required_fields,
                "exact_table_names": [ref["full_name"] for ref in exact_table_refs],
                "missing_exact_tables": missing_exact_tables,
                "seed_tables": [ref["full_name"] for ref in seed_table_refs],
                "semantic_contracts": semantic_contracts,
            }

            return ToolResult(
                success=True,
                result_for_llm=llm_content,
                ui_component=ui_component,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Schema retrieval failed: {e}", exc_info=True)
            error_message = f"Schema retrieval failed: {str(e)}"
            fallback_metadata = {
                "tool_name": self.name,
                "search_mode": effective_search_mode.value,
                "graph_hint": args.graph_hint.value,
                "query": args.query,
                "total_results": 0,
                "requested_limit": args.limit,
                "effective_limit": min(args.limit, self._max_initial_results),
                "selected_tables": [],
                "selected_table_refs": [],
                "selected_columns": {},
                "selected_primary_keys": {},
                "selected_column_refs": [],
                "domain_filter": args.domain_filter,
                "required_fields": required_fields,
                "exact_table_names": [ref["full_name"] for ref in exact_table_refs],
                "missing_exact_tables": [ref["full_name"] for ref in exact_table_refs],
                "seed_tables": [ref["full_name"] for ref in seed_table_refs],
                "semantic_contracts": semantic_contracts,
            }

            return ToolResult(
                success=False,
                result_for_llm=error_message,
                ui_component=None,
                error=str(e),
                metadata=fallback_metadata,
            )

    def _format_result_for_llm(
        self,
        results: List[Any],
        search_mode: SearchMode,
        query: str,
        required_fields: Optional[List[str]] = None,
        include_all_fields: bool = False,
    ) -> str:
        """Format search results for LLM consumption.

        Args:
            results: Search results from SchemaMemory
            search_mode: The search mode used
            query: Original search query

        Returns:
            Formatted string for LLM
        """
        if not results:
            return f"No table schemas found matching '{query}'"

        lines = [
            f"【Schema Retrieval Results】Mode: {search_mode.value}",
            f"Found {len(results)} related table(s):\n"
        ]

        for i, result in enumerate(results, 1):
            schema = result.table_schema
            if not schema:
                continue

            lines.append(f"--- Table {i}: {schema.full_name} ---")
            lines.append(f"Domain: {schema.business_context.domain}")
            lines.append(f"Description: {schema.business_context.description}")
            lines.append("Fields:")

            fields_to_display = list(schema.field_definitions)
            if not include_all_fields:
                fields_to_display = fields_to_display[:15]
                required_names = {
                    str(field_name).strip().strip("`\"[]").split(".")[-1].lower()
                    for field_name in (required_fields or [])
                    if str(field_name).strip()
                }
                displayed_names = {
                    field.field_name.lower() for field in fields_to_display
                }
                fields_to_display.extend(
                    field
                    for field in schema.field_definitions[15:]
                    if field.field_name.lower() in required_names
                    and field.field_name.lower() not in displayed_names
                )

            for field in fields_to_display:
                pk = " [PK]" if field.is_primary_key else ""
                fk = " [FK]" if field.is_foreign_key else ""
                desc = field.business_meaning or field.description or ""
                lines.append(
                    f"  - {field.field_name} ({field.data_type}){pk}{fk}: {desc}"
                )

            if result.match_reason:
                lines.append(f"Match reason: {result.match_reason}")

            lines.append("")

        return "\n".join(lines)

    def _build_ui_component(
        self,
        results: List[Any],
        query: str,
        search_mode: SearchMode
    ) -> UiComponent:
        """Build UI component for displaying results.

        Args:
            results: Search results from SchemaMemory
            query: Original search query
            search_mode: The search mode used

        Returns:
            UiComponent with schema retrieval results
        """
        # Search mode display names
        mode_display_names = {
            SearchMode.HYBRID: "⚖️ Hybrid Search",
            SearchMode.VECTOR: "🔍 Vector Search",
            SearchMode.GRAPH: "🔗 Graph Search",
            SearchMode.EXPAND: "🌱 Expand Search",
            SearchMode.DIRECT: "🎯 Direct Table Lookup",
        }
        similarity_label_by_mode = {
            SearchMode.HYBRID: "Similarity (RRF Score ×100)",
            SearchMode.VECTOR: "Similarity (Cosine-Based)",
            SearchMode.GRAPH: "Graph Match",
            SearchMode.EXPAND: "Similarity (Hop-Based)",
            SearchMode.DIRECT: "Exact Schema Memory Match",
        }

        # Build table display list
        tables_display = []
        for result in results:
            schema = result.table_schema
            if not schema:
                continue

            # Build field display list
            fields_display = []
            for field in schema.field_definitions[:15]:  # Limit fields for UI
                fk_ref = None
                if field.foreign_key:
                    fk_ref = field.foreign_key.full_path

                field_display = SchemaFieldDisplay(
                    field_name=field.field_name,
                    data_type=field.data_type,
                    is_primary_key=field.is_primary_key,
                    is_foreign_key=field.is_foreign_key,
                    is_nullable=field.is_nullable,
                    business_meaning=field.business_meaning or field.description or "",
                    foreign_key_ref=fk_ref,
                )
                fields_display.append(field_display)

            table_display = SchemaTableDisplay(
                full_name=schema.full_name,
                schema_name=schema.schema_name,
                table_name=schema.table_name,
                domain=schema.business_context.domain,
                description=schema.business_context.description,
                fields=fields_display,
                similarity_score=result.similarity_score,
                match_reason=result.match_reason,
            )
            tables_display.append(table_display)

        result_count = len(tables_display)
        mode_label = mode_display_names.get(search_mode, search_mode.value)
        result_label = "1 Result" if result_count == 1 else f"{result_count} Results"
        title = f"Schema Retrieval Results: {result_label}"
        subtitle = f"{mode_label} | Query: {query}"

        content_lines = [
            f"**Search Mode:** {mode_label}",
            f"**Query:** `{query}`",
            f"**Results:** {result_count}",
        ]

        if not tables_display:
            content_lines.extend(
                [
                    "",
                    "No table schemas matched the current filters.",
                ]
            )
        else:
            for table in tables_display[:5]:
                field_preview = ", ".join(
                    field.field_name for field in table.fields[:6]
                )
                if len(table.fields) > 6:
                    field_preview = f"{field_preview}, ..."

                content_lines.extend(
                    [
                        "",
                        f"### `{table.full_name}`",
                        f"- **Domain:** {table.domain or 'Unknown'}",
                        f"- **Description:** {table.description or 'N/A'}",
                        f"- **Fields:** {field_preview or 'None'}",
                    ]
                )

                if table.similarity_score is not None:
                    similarity_label = similarity_label_by_mode.get(
                        search_mode,
                        "Similarity",
                    )
                    display_score = table.similarity_score
                    if search_mode == SearchMode.HYBRID:
                        display_score = table.similarity_score * 100
                    content_lines.append(
                        f"- **{similarity_label}:** {display_score:.2f}"
                    )
                if table.match_reason:
                    content_lines.append(f"- **Match:** {table.match_reason}")

            if len(tables_display) > 5:
                content_lines.extend(
                    [
                        "",
                        f"_...and {len(tables_display) - 5} more table(s)._",
                    ]
                )

        # Create card component
        card = SchemaRetrieveCardComponent(
            title=title,
            subtitle=subtitle,
            content="\n".join(content_lines),
            search_mode=mode_label,
            query=query,
            tables=tables_display,
            total_count=result_count,
            collapsible=True,
            collapsed=False,
            markdown=True,
        )

        # Simple text summary
        table_names = [t.full_name for t in tables_display[:5]]
        if not tables_display:
            summary = f"No table schemas found matching '{query}'"
        elif len(tables_display) > 5:
            summary = (
                f"Found {len(tables_display)} related table(s): "
                f"{', '.join(table_names)}, ..."
            )
        else:
            summary = f"Found {len(tables_display)} related table(s): {', '.join(table_names)}"

        return UiComponent(
            rich_component=card,
            simple_component=SimpleTextComponent(text=summary),
        )
