"""Tests for memory.similarity (find_similar + build_clusters BFS)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from mem_mcp.memory.similarity import (
    DEFAULT_SIMILARITY_THRESHOLD,
    SimilarMemory,
    build_clusters,
    find_similar,
)


class TestFindSimilar:
    @pytest.mark.asyncio
    async def test_returns_ranked_results(self) -> None:
        """find_similar returns rows mapped to SimilarMemory dataclass + sim."""
        conn = AsyncMock()
        conn.fetch.return_value = [
            {
                "id": uuid4(),
                "content": "match A",
                "type": "note",
                "tags": ["foo"],
                "similarity": 0.93,
            },
            {
                "id": uuid4(),
                "content": "match B",
                "type": "note",
                "tags": [],
                "similarity": 0.88,
            },
        ]
        out = await find_similar(
            conn,
            uuid4(),
            [0.1] * 1536,
            threshold=0.85,
            limit=10,
        )
        assert len(out) == 2
        assert all(isinstance(s, SimilarMemory) for s in out)
        assert out[0].similarity == 0.93
        assert out[0].content == "match A"

    @pytest.mark.asyncio
    async def test_sql_excludes_seed_id_when_provided(self) -> None:
        """When exclude_id is given, it's threaded into the SQL params."""
        conn = AsyncMock()
        conn.fetch.return_value = []
        seed_id = uuid4()
        await find_similar(
            conn, uuid4(), [0.0] * 1536, exclude_id=seed_id, threshold=0.5
        )
        # args[0]=SQL, args[1]=vec, args[2]=tenant_id, args[3]=exclude_id,
        # args[4]=threshold, args[5]=limit
        args = conn.fetch.call_args.args
        assert args[3] == seed_id
        assert args[4] == 0.5

    @pytest.mark.asyncio
    async def test_default_threshold_constant(self) -> None:
        """DEFAULT_SIMILARITY_THRESHOLD is the constant we ship in the UI default."""
        # Sanity guard so the UI default and the helper default don't drift.
        assert 0.7 <= DEFAULT_SIMILARITY_THRESHOLD <= 0.95


class TestBuildClusters:
    @pytest.mark.asyncio
    async def test_empty_corpus_returns_empty(self) -> None:
        conn = AsyncMock()
        conn.fetch.return_value = []
        out = await build_clusters(conn, uuid4())
        assert out == []

    @pytest.mark.asyncio
    async def test_singletons_are_dropped(self) -> None:
        """Memories with no above-threshold neighbors are NOT returned as
        clusters (clusters require >= 2 members)."""
        a, b, c = uuid4(), uuid4(), uuid4()
        conn = AsyncMock()
        # First fetch: list of all memory ids
        # Subsequent fetches: each call to find_neighbor_ids returns empty
        conn.fetch.side_effect = [
            [{"id": a}, {"id": b}, {"id": c}],  # corpus
            [],  # neighbors(a)
            [],  # neighbors(b)
            [],  # neighbors(c)
        ]
        out = await build_clusters(conn, uuid4())
        assert out == []

    @pytest.mark.asyncio
    async def test_two_member_cluster(self) -> None:
        """Two memories that point to each other form a single 2-member cluster."""
        a, b, c = uuid4(), uuid4(), uuid4()
        conn = AsyncMock()
        conn.fetch.side_effect = [
            [{"id": a}, {"id": b}, {"id": c}],  # corpus
            [{"id": b}],  # neighbors(a) -> [b]
            [{"id": a}],  # neighbors(b) -> [a] (already in cluster, no expand)
            [],  # neighbors(c) -> singleton, dropped
        ]
        out = await build_clusters(conn, uuid4())
        assert len(out) == 1
        assert set(out[0]) == {a, b}

    @pytest.mark.asyncio
    async def test_transitive_closure_pulls_three(self) -> None:
        """a~b, b~c (but not a~c directly) yields a 3-member cluster via
        transitive closure during BFS."""
        a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
        conn = AsyncMock()
        conn.fetch.side_effect = [
            [{"id": a}, {"id": b}, {"id": c}, {"id": d}],  # corpus
            [{"id": b}],  # neighbors(a) -> [b]
            [{"id": c}],  # neighbors(b) -> [c] (still in a's cluster expansion)
            [{"id": b}],  # neighbors(c) -> [b] (already in cluster)
            [],  # neighbors(d) -> singleton, dropped
        ]
        out = await build_clusters(conn, uuid4())
        assert len(out) == 1
        assert set(out[0]) == {a, b, c}

    @pytest.mark.asyncio
    async def test_two_separate_clusters(self) -> None:
        """Two disjoint connected components yield two clusters."""
        a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
        conn = AsyncMock()
        # Use a side_effect callable so we can match by SQL+args (the corpus
        # query has different param shape than neighbor queries).
        call_count = {"n": 0}
        # Determine neighbors per node based on argument inspection.
        neighbors_map: dict[UUID, list[UUID]] = {a: [b], b: [a], c: [d], d: [c]}

        async def fake_fetch(*args, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [{"id": a}, {"id": b}, {"id": c}, {"id": d}]
            # neighbor queries: args[1] = seed_id (after SQL string at args[0])
            seed_id = args[1] if len(args) > 1 else kwargs.get("seed_id")
            assert isinstance(seed_id, UUID)
            return [{"id": nid} for nid in neighbors_map.get(seed_id, [])]

        conn.fetch.side_effect = fake_fetch
        out = await build_clusters(conn, uuid4())
        assert len(out) == 2
        all_members = {m for c in out for m in c}
        assert all_members == {a, b, c, d}
        sizes = sorted(len(c) for c in out)
        assert sizes == [2, 2]
