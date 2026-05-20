"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { MarkdownContent } from "@/components/ui/MarkdownContent";
import { csrfHeaders } from "@/lib/csrf";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MemoryRow {
  id: string;
  type: string;
  content: string;
  tags: string[];
  created_at?: string;
  updated_at?: string;
  last_accessed_at?: string | null;
  access_count?: number;
}

interface BrowseResponse {
  results: MemoryRow[];
  next_cursor?: string;
}

interface StaleResponse {
  mode: "updated" | "accessed";
  days: number;
  results: MemoryRow[];
}

type Tab = "browse" | "stale" | "similar";

interface SimilarResult {
  id: string;
  content: string;
  type: string;
  tags: string[];
  similarity: number;
}

interface ClusterMember {
  id: string;
  content: string;
  type: string;
  tags: string[];
  updated_at: string;
  last_accessed_at: string | null;
}

interface Cluster {
  cluster_id: string;
  seed_memory_id: string;
  member_count: number;
  similarity_threshold: number;
  built_at: string;
  members: ClusterMember[];
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function MemoriesView({
  allTags,
  initialTab,
  initialType,
  initialTag,
}: {
  allTags: string[];
  initialTab: Tab;
  initialType?: string;
  initialTag?: string;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  // Set when user clicks "Find similar" on a row — pre-fills the Similar tab.
  const [similarSeedId, setSimilarSeedId] = useState<string | null>(null);

  const openSimilarFor = (seedId: string) => {
    setSimilarSeedId(seedId);
    setTab("similar");
  };

  return (
    <div className="space-y-4">
      <div className="border-b flex gap-6 text-sm">
        <TabButton active={tab === "browse"} onClick={() => setTab("browse")}>
          Browse
        </TabButton>
        <TabButton active={tab === "stale"} onClick={() => setTab("stale")}>
          Stale
        </TabButton>
        <TabButton active={tab === "similar"} onClick={() => setTab("similar")}>
          Similar
        </TabButton>
      </div>
      {tab === "browse" && (
        <BrowseTab
          allTags={allTags}
          initialType={initialType}
          initialTag={initialTag}
          onFindSimilar={openSimilarFor}
        />
      )}
      {tab === "stale" && <StaleTab onFindSimilar={openSimilarFor} />}
      {tab === "similar" && <SimilarTab seedId={similarSeedId} onSeedChange={setSimilarSeedId} />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`pb-2 -mb-px border-b-2 ${
        active ? "border-primary text-primary font-medium" : "border-transparent text-ink-muted"
      }`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Browse tab
// ---------------------------------------------------------------------------

function BrowseTab({
  allTags,
  initialType,
  initialTag,
  onFindSimilar,
}: {
  allTags: string[];
  initialType?: string;
  initialTag?: string;
  onFindSimilar: (id: string) => void;
}) {
  const [selectedTags, setSelectedTags] = useState<string[]>(
    initialTag ? initialTag.split(",").map((t) => t.trim()).filter(Boolean) : []
  );
  const [type, setType] = useState<string>(initialType ?? "");
  const [results, setResults] = useState<MemoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const fetchResults = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams();
    qs.set("limit", "100");
    if (type) qs.set("type", type);
    for (const t of selectedTags) qs.append("tag", t);
    try {
      const res = await fetch(`/api/web/memories?${qs.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`Failed (${res.status})`);
        setResults([]);
        return;
      }
      const data: BrowseResponse = await res.json();
      setResults(data.results ?? []);
    } catch (e) {
      setError(String(e));
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [type, selectedTags]);

  useEffect(() => {
    void fetchResults();
  }, [fetchResults]);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllVisible = () => setSelected(new Set(results.map((r) => r.id)));
  const clearSelection = () => setSelected(new Set());

  const addTag = (t: string) => {
    const trimmed = t.trim();
    if (!trimmed) return;
    if (selectedTags.includes(trimmed)) return;
    setSelectedTags([...selectedTags, trimmed]);
  };

  const onBulkDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`Soft-delete ${ids.length} memor${ids.length === 1 ? "y" : "ies"}? You can recover within 30 days.`)) return;
    setBulkBusy(true);
    try {
      const res = await fetch("/api/web/memories/management/bulk-delete", {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(`Failed: ${body.detail?.message ?? res.statusText}`);
        return;
      }
      const data = await res.json();
      // Drop deleted rows from the visible list.
      const deletedIds = new Set(
        (data.results ?? [])
          .filter((r: { status: string }) => r.status === "deleted")
          .map((r: { id: string }) => r.id)
      );
      setResults((prev) => prev.filter((m) => !deletedIds.has(m.id)));
      clearSelection();
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Filters
        allTags={allTags}
        selectedTags={selectedTags}
        setSelectedTags={setSelectedTags}
        type={type}
        setType={setType}
      />
      <ResultsToolbar
        count={results.length}
        selectedCount={selected.size}
        onSelectAll={selectAllVisible}
        onClear={clearSelection}
        onBulkDelete={onBulkDelete}
        busy={bulkBusy}
      />
      {error && <p className="text-danger text-sm">{error}</p>}
      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : (
        <MemoryList
          rows={results}
          selected={selected}
          onToggle={toggleSelected}
          onFindSimilar={onFindSimilar}
          onAddTag={addTag}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stale tab
// ---------------------------------------------------------------------------

function StaleTab({ onFindSimilar }: { onFindSimilar: (id: string) => void }) {
  const [mode, setMode] = useState<"updated" | "accessed">("updated");
  const [days, setDays] = useState(90);
  const [results, setResults] = useState<MemoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const fetchResults = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({
      mode,
      days: String(days),
      limit: "100",
    });
    try {
      const res = await fetch(`/api/web/memories/management/stale?${qs.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`Failed (${res.status})`);
        setResults([]);
        return;
      }
      const data: StaleResponse = await res.json();
      setResults(data.results ?? []);
    } catch (e) {
      setError(String(e));
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [mode, days]);

  useEffect(() => {
    void fetchResults();
  }, [fetchResults]);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const selectAllVisible = () => setSelected(new Set(results.map((r) => r.id)));
  const clearSelection = () => setSelected(new Set());

  const onBulkDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`Soft-delete ${ids.length} stale memor${ids.length === 1 ? "y" : "ies"}? Recoverable for 30 days.`)) return;
    setBulkBusy(true);
    try {
      const res = await fetch("/api/web/memories/management/bulk-delete", {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        alert(`Failed (${res.status})`);
        return;
      }
      const data = await res.json();
      const deletedIds = new Set(
        (data.results ?? [])
          .filter((r: { status: string }) => r.status === "deleted")
          .map((r: { id: string }) => r.id)
      );
      setResults((prev) => prev.filter((m) => !deletedIds.has(m.id)));
      clearSelection();
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4 items-end p-4 bg-surface-muted rounded">
        <label className="flex flex-col">
          <span className="text-xs text-ink-muted mb-1">Staleness measure</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "updated" | "accessed")}
            className="border rounded px-2 py-1 bg-white"
          >
            <option value="updated">Not updated</option>
            <option value="accessed">Not read</option>
          </select>
        </label>
        <label className="flex flex-col">
          <span className="text-xs text-ink-muted mb-1">Older than</span>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="border rounded px-2 py-1 bg-white"
          >
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
            <option value={365}>1 year</option>
          </select>
        </label>
        {mode === "accessed" && (
          <p className="text-xs text-ink-muted max-w-md">
            Read-tracking started recently. Memories last accessed before tracking shipped show
            as "never accessed" and rank as the stalest.
          </p>
        )}
      </div>
      <ResultsToolbar
        count={results.length}
        selectedCount={selected.size}
        onSelectAll={selectAllVisible}
        onClear={clearSelection}
        onBulkDelete={onBulkDelete}
        busy={bulkBusy}
      />
      {error && <p className="text-danger text-sm">{error}</p>}
      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : results.length === 0 ? (
        <p className="text-sm text-ink-muted">
          Nothing stale — all memories were touched within the last {days} days.
        </p>
      ) : (
        <MemoryList
          rows={results}
          selected={selected}
          onToggle={toggleSelected}
          showStaleMeta={mode}
          onFindSimilar={onFindSimilar}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Similar tab (Phase 2 — Shape A find_similar + Shape B precomputed clusters)
// ---------------------------------------------------------------------------

function SimilarTab({
  seedId,
  onSeedChange,
}: {
  seedId: string | null;
  onSeedChange: (id: string | null) => void;
}) {
  type SimilarMode = "find" | "clusters";
  const [mode, setMode] = useState<SimilarMode>(seedId ? "find" : "clusters");
  const [queryText, setQueryText] = useState("");
  const [threshold, setThreshold] = useState(0.85);
  const [results, setResults] = useState<SimilarResult[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [clustersBuiltAt, setClustersBuiltAt] = useState<string | null>(null);

  const fetchSimilar = useCallback(async () => {
    if (mode !== "find") return;
    if (!seedId && !queryText.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const body: {
        seed_id?: string;
        query_text?: string;
        threshold: number;
        limit: number;
      } = { threshold, limit: 50 };
      if (seedId) body.seed_id = seedId;
      else body.query_text = queryText;
      const res = await fetch("/api/web/memories/management/similar", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setError(detail.detail?.message ?? detail.detail ?? `Failed (${res.status})`);
        setResults([]);
        return;
      }
      const data = await res.json();
      setResults(data.results ?? []);
    } catch (e) {
      setError(String(e));
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [mode, seedId, queryText, threshold]);

  const fetchClusters = useCallback(async () => {
    if (mode !== "clusters") return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/web/memories/management/clusters?limit=50", {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`Failed (${res.status})`);
        setClusters([]);
        return;
      }
      const data = await res.json();
      setClusters(data.clusters ?? []);
      setClustersBuiltAt(data.built_at ?? null);
    } catch (e) {
      setError(String(e));
      setClusters([]);
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    void fetchSimilar();
  }, [fetchSimilar]);
  useEffect(() => {
    void fetchClusters();
  }, [fetchClusters]);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onBulkDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`Soft-delete ${ids.length} memor${ids.length === 1 ? "y" : "ies"}? Recoverable for 30 days.`)) return;
    setBulkBusy(true);
    try {
      const res = await fetch("/api/web/memories/management/bulk-delete", {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        alert(`Failed (${res.status})`);
        return;
      }
      const data = await res.json();
      const deletedIds = new Set(
        (data.results ?? [])
          .filter((r: { status: string }) => r.status === "deleted")
          .map((r: { id: string }) => r.id)
      );
      setResults((prev) => prev.filter((m) => !deletedIds.has(m.id)));
      setClusters((prev) =>
        prev
          .map((c) => ({ ...c, members: c.members.filter((m) => !deletedIds.has(m.id)) }))
          .filter((c) => c.members.length >= 2)
      );
      setSelected(new Set());
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4 items-end p-4 bg-surface-muted rounded">
        <label className="flex flex-col">
          <span className="text-xs text-ink-muted mb-1">Mode</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SimilarMode)}
            className="border rounded px-2 py-1 bg-white"
          >
            <option value="find">Find similar to…</option>
            <option value="clusters">Browse clusters</option>
          </select>
        </label>
        {mode === "find" && (
          <>
            {seedId ? (
              <div className="flex flex-col">
                <span className="text-xs text-ink-muted mb-1">Seeded by memory</span>
                <code className="text-xs bg-white border rounded px-2 py-1">
                  {seedId.slice(0, 8)}…{" "}
                  <button
                    onClick={() => onSeedChange(null)}
                    className="text-primary ml-2"
                  >
                    clear
                  </button>
                </code>
              </div>
            ) : (
              <label className="flex flex-col flex-1 min-w-[320px]">
                <span className="text-xs text-ink-muted mb-1">Query text</span>
                <input
                  value={queryText}
                  onChange={(e) => setQueryText(e.target.value)}
                  placeholder="paste any text — we'll find memories like it"
                  className="border rounded px-2 py-1 bg-white"
                />
              </label>
            )}
            <label className="flex flex-col">
              <span className="text-xs text-ink-muted mb-1">
                Threshold: {threshold.toFixed(2)}
              </span>
              <input
                type="range"
                min="0.5"
                max="0.99"
                step="0.01"
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value))}
                className="w-48"
              />
            </label>
          </>
        )}
        {mode === "clusters" && clustersBuiltAt && (
          <span className="text-xs text-ink-muted">
            Last built: {new Date(clustersBuiltAt).toLocaleString()}
          </span>
        )}
        {mode === "clusters" && !clustersBuiltAt && (
          <span className="text-xs text-warn">
            No clusters yet. The cluster build job runs weekly; until first run, this is empty.
          </span>
        )}
      </div>

      {error && <p className="text-danger text-sm">{error}</p>}
      <ResultsToolbar
        count={mode === "find" ? results.length : clusters.length}
        selectedCount={selected.size}
        onSelectAll={() => {
          const ids =
            mode === "find"
              ? results.map((r) => r.id)
              : clusters.flatMap((c) => c.members.map((m) => m.id));
          setSelected(new Set(ids));
        }}
        onClear={() => setSelected(new Set())}
        onBulkDelete={onBulkDelete}
        busy={bulkBusy}
      />

      {loading && <p className="text-sm text-ink-muted">Loading…</p>}

      {!loading && mode === "find" && (
        <SimilarResultsList
          rows={results}
          selected={selected}
          onToggle={toggleSelected}
        />
      )}
      {!loading && mode === "clusters" && (
        <ClustersList
          clusters={clusters}
          selected={selected}
          onToggle={toggleSelected}
        />
      )}
    </div>
  );
}

function SimilarResultsList({
  rows,
  selected,
  onToggle,
}: {
  rows: SimilarResult[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-ink-muted">No similar memories above threshold.</p>;
  }
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li
          key={r.id}
          className={`border rounded p-3 flex gap-3 ${
            selected.has(r.id) ? "bg-primary-muted border-primary" : "hover:bg-surface-muted"
          }`}
        >
          <input
            type="checkbox"
            checked={selected.has(r.id)}
            onChange={() => onToggle(r.id)}
            className="mt-1 shrink-0"
          />
          <div className="flex-1 min-w-0">
            <div className="flex justify-between items-start mb-1">
              <span className="text-xs uppercase text-ink-muted">
                {r.type} · sim {r.similarity.toFixed(3)}
              </span>
              <Link
                href={`/memories/${r.id}`}
                className="text-xs text-primary hover:underline font-mono"
              >
                {r.id.slice(0, 8)}
              </Link>
            </div>
            <MarkdownContent content={r.content} variant="preview" />
            <div className="mt-1.5 flex flex-wrap gap-1">
              {r.tags.map((t) => (
                <span key={t} className="text-xs bg-surface-subtle px-1.5 py-0.5 rounded">
                  {t}
                </span>
              ))}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function ClustersList({
  clusters,
  selected,
  onToggle,
}: {
  clusters: Cluster[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (clusters.length === 0) {
    return <p className="text-sm text-ink-muted">No clusters detected.</p>;
  }
  return (
    <ul className="space-y-4">
      {clusters.map((c) => (
        <li key={c.cluster_id} className="border rounded p-4 bg-white">
          <div className="flex justify-between items-baseline mb-3">
            <h3 className="text-sm font-medium">
              Cluster of {c.member_count}{" "}
              <span className="text-ink-muted font-normal">
                · threshold {c.similarity_threshold.toFixed(2)}
              </span>
            </h3>
            <span className="text-xs text-ink-faint">
              built {new Date(c.built_at).toLocaleDateString()}
            </span>
          </div>
          <ul className="space-y-2">
            {c.members.map((m) => (
              <li
                key={m.id}
                className={`flex gap-2 items-start p-2 rounded ${
                  selected.has(m.id) ? "bg-primary-muted" : ""
                } ${m.id === c.seed_memory_id ? "border-l-2 border-primary pl-2" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={selected.has(m.id)}
                  onChange={() => onToggle(m.id)}
                  className="mt-1 shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between items-start">
                    <span className="text-xs uppercase text-ink-muted">
                      {m.type}
                      {m.id === c.seed_memory_id && (
                        <span className="ml-1 text-primary">· seed</span>
                      )}
                    </span>
                    <Link
                      href={`/memories/${m.id}`}
                      className="text-xs text-primary hover:underline font-mono"
                    >
                      {m.id.slice(0, 8)}
                    </Link>
                  </div>
                  <div className="mt-0.5">
                    <MarkdownContent content={m.content} variant="preview" />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function Filters({
  allTags,
  selectedTags,
  setSelectedTags,
  type,
  setType,
}: {
  allTags: string[];
  selectedTags: string[];
  setSelectedTags: (t: string[]) => void;
  type: string;
  setType: (t: string) => void;
}) {
  const [tagInput, setTagInput] = useState("");

  const addTag = (t: string) => {
    const trimmed = t.trim();
    if (!trimmed) return;
    if (selectedTags.includes(trimmed)) return;
    setSelectedTags([...selectedTags, trimmed]);
    setTagInput("");
  };
  const removeTag = (t: string) => setSelectedTags(selectedTags.filter((x) => x !== t));

  const suggestions = useMemo(() => {
    if (!tagInput.trim()) return [];
    const q = tagInput.toLowerCase();
    return allTags
      .filter((t) => t.toLowerCase().includes(q) && !selectedTags.includes(t))
      .slice(0, 8);
  }, [tagInput, allTags, selectedTags]);

  return (
    <div className="space-y-3 p-4 bg-surface-muted rounded">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="flex flex-col">
          <span className="text-xs text-ink-muted mb-1">Type</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="border rounded px-2 py-1 bg-white"
          >
            <option value="">all</option>
            <option value="note">note</option>
            <option value="decision">decision</option>
            <option value="fact">fact</option>
            <option value="snippet">snippet</option>
            <option value="question">question</option>
          </select>
        </label>
        <label className="flex flex-col flex-1 min-w-[240px] relative">
          <span className="text-xs text-ink-muted mb-1">Tags</span>
          <input
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addTag(tagInput);
              }
            }}
            placeholder="type to search; Enter to add"
            className="border rounded px-2 py-1 bg-white"
          />
          {suggestions.length > 0 && (
            <ul className="absolute top-full left-0 right-0 bg-white border rounded shadow-sm z-10 mt-1 max-h-48 overflow-auto">
              {suggestions.map((s) => (
                <li
                  key={s}
                  onClick={() => addTag(s)}
                  className="px-2 py-1 hover:bg-primary-muted cursor-pointer text-sm"
                >
                  {s}
                </li>
              ))}
            </ul>
          )}
        </label>
      </div>
      {selectedTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selectedTags.map((t) => (
            <button
              key={t}
              onClick={() => removeTag(t)}
              className="inline-flex items-center gap-1 text-xs bg-primary-muted text-primary-hover px-2 py-1 rounded hover:bg-primary-muted"
            >
              {t}
              <span className="text-primary">×</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ResultsToolbar({
  count,
  selectedCount,
  onSelectAll,
  onClear,
  onBulkDelete,
  busy,
}: {
  count: number;
  selectedCount: number;
  onSelectAll: () => void;
  onClear: () => void;
  onBulkDelete: () => void;
  busy: boolean;
}) {
  return (
    <div className="flex justify-between items-center text-sm">
      <span className="text-ink-muted">
        {count} result{count === 1 ? "" : "s"}
        {selectedCount > 0 ? ` · ${selectedCount} selected` : ""}
      </span>
      <div className="flex gap-3">
        {selectedCount > 0 ? (
          <>
            <button onClick={onClear} className="text-ink-muted hover:underline">
              Clear
            </button>
            <button
              onClick={onBulkDelete}
              disabled={busy}
              className="rounded bg-danger text-white px-3 py-1 disabled:opacity-50"
            >
              {busy ? "Deleting…" : `Delete ${selectedCount}`}
            </button>
          </>
        ) : (
          <button onClick={onSelectAll} disabled={count === 0} className="text-ink-muted hover:underline disabled:opacity-50">
            Select all
          </button>
        )}
      </div>
    </div>
  );
}

function MemoryList({
  rows,
  selected,
  onToggle,
  showStaleMeta,
  onFindSimilar,
  onAddTag,
}: {
  rows: MemoryRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  showStaleMeta?: "updated" | "accessed";
  onFindSimilar?: (id: string) => void;
  onAddTag?: (tag: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-ink-muted">No memories match.</p>;
  }
  return (
    <ul className="space-y-2">
      {rows.map((m) => (
        <li
          key={m.id}
          className={`border rounded p-3 flex gap-3 ${
            selected.has(m.id) ? "bg-primary-muted border-primary" : "hover:bg-surface-muted"
          }`}
        >
          <input
            type="checkbox"
            checked={selected.has(m.id)}
            onChange={() => onToggle(m.id)}
            className="mt-1 shrink-0"
          />
          <div className="flex-1 min-w-0">
            <div className="flex justify-between items-start mb-1">
              <span className="text-xs uppercase text-ink-muted">{m.type}</span>
              <div className="flex items-center gap-2">
                {onFindSimilar && (
                  <button
                    onClick={() => onFindSimilar(m.id)}
                    className="text-xs text-primary hover:underline"
                    title="Find similar memories"
                  >
                    similar →
                  </button>
                )}
                <Link
                  href={`/memories/${m.id}`}
                  className="text-xs text-primary hover:underline font-mono"
                >
                  {m.id.slice(0, 8)}
                </Link>
              </div>
            </div>
            <MarkdownContent content={m.content} variant="preview" />
            <div className="mt-1.5 flex flex-wrap gap-1">
              {m.tags.map((t) => (
                <button
                  key={t}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onAddTag?.(t);
                  }}
                  type="button"
                  className="text-xs bg-surface-subtle hover:bg-surface-strong px-1.5 py-0.5 rounded cursor-pointer transition-colors"
                  title="Add this tag to filter"
                >
                  {t}
                </button>
              ))}
            </div>
            {showStaleMeta && (
              <p className="text-xs text-ink-muted mt-1.5">
                {showStaleMeta === "updated" ? "Updated" : "Last read"}:{" "}
                {showStaleMeta === "updated"
                  ? formatDate(m.updated_at)
                  : m.last_accessed_at
                  ? formatDate(m.last_accessed_at)
                  : "never"}{" "}
                {showStaleMeta === "accessed" && m.access_count !== undefined && (
                  <span className="text-ink-faint">· {m.access_count} reads</span>
                )}
              </p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
