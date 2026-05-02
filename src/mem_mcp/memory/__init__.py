"""Memory primitives — normalize, dedupe, hybrid retrieval, recency.

Per LLD §4.5. The actual MCP tools (memory.write/search/get/list/...) wire
these primitives together; they're added in T-5.9 onwards.
"""

from mem_mcp.memory.dedupe import DedupeMatch, check_dup
from mem_mcp.memory.hybrid_query import (
    SEARCH_DEFAULT_W_KW,
    SEARCH_DEFAULT_W_SEM,
    SearchParams,
    SearchResult,
    hybrid_search,
)
from mem_mcp.memory.normalize import hash_content, normalize_for_hash
from mem_mcp.memory.recency import (
    RECENCY_LAMBDA_BY_TYPE,
    recency_lambda_for,
)

__all__ = [
    "DedupeMatch",
    "RECENCY_LAMBDA_BY_TYPE",
    "SEARCH_DEFAULT_W_KW",
    "SEARCH_DEFAULT_W_SEM",
    "SearchParams",
    "SearchResult",
    "check_dup",
    "hash_content",
    "hybrid_search",
    "normalize_for_hash",
    "recency_lambda_for",
]
