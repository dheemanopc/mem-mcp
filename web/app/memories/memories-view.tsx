"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

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

type Tab = "browse" | "stale";

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

  return (
    <div className="space-y-4">
      <div className="border-b flex gap-6 text-sm">
        <TabButton active={tab === "browse"} onClick={() => setTab("browse")}>
          Browse
        </TabButton>
        <TabButton active={tab === "stale"} onClick={() => setTab("stale")}>
          Stale
        </TabButton>
      </div>
      {tab === "browse" && (
        <BrowseTab allTags={allTags} initialType={initialType} initialTag={initialTag} />
      )}
      {tab === "stale" && <StaleTab />}
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
        active ? "border-blue-600 text-blue-600 font-medium" : "border-transparent text-gray-600"
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
}: {
  allTags: string[];
  initialType?: string;
  initialTag?: string;
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

  const onBulkDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`Soft-delete ${ids.length} memor${ids.length === 1 ? "y" : "ies"}? You can recover within 30 days.`)) return;
    setBulkBusy(true);
    try {
      const res = await fetch("/api/web/memories/management/bulk-delete", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
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
      {error && <p className="text-red-600 text-sm">{error}</p>}
      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <MemoryList rows={results} selected={selected} onToggle={toggleSelected} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stale tab
// ---------------------------------------------------------------------------

function StaleTab() {
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
        headers: { "content-type": "application/json" },
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
      <div className="flex flex-wrap gap-4 items-end p-4 bg-gray-50 rounded">
        <label className="flex flex-col">
          <span className="text-xs text-gray-600 mb-1">Staleness measure</span>
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
          <span className="text-xs text-gray-600 mb-1">Older than</span>
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
          <p className="text-xs text-gray-500 max-w-md">
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
      {error && <p className="text-red-600 text-sm">{error}</p>}
      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : results.length === 0 ? (
        <p className="text-sm text-gray-500">
          Nothing stale — all memories were touched within the last {days} days.
        </p>
      ) : (
        <MemoryList rows={results} selected={selected} onToggle={toggleSelected} showStaleMeta={mode} />
      )}
    </div>
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
    <div className="space-y-3 p-4 bg-gray-50 rounded">
      <div className="flex flex-wrap gap-3 items-end">
        <label className="flex flex-col">
          <span className="text-xs text-gray-600 mb-1">Type</span>
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
          <span className="text-xs text-gray-600 mb-1">Tags</span>
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
                  className="px-2 py-1 hover:bg-blue-50 cursor-pointer text-sm"
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
              className="inline-flex items-center gap-1 text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded hover:bg-blue-200"
            >
              {t}
              <span className="text-blue-500">×</span>
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
      <span className="text-gray-500">
        {count} result{count === 1 ? "" : "s"}
        {selectedCount > 0 ? ` · ${selectedCount} selected` : ""}
      </span>
      <div className="flex gap-3">
        {selectedCount > 0 ? (
          <>
            <button onClick={onClear} className="text-gray-600 hover:underline">
              Clear
            </button>
            <button
              onClick={onBulkDelete}
              disabled={busy}
              className="rounded bg-red-600 text-white px-3 py-1 disabled:opacity-50"
            >
              {busy ? "Deleting…" : `Delete ${selectedCount}`}
            </button>
          </>
        ) : (
          <button onClick={onSelectAll} disabled={count === 0} className="text-gray-600 hover:underline disabled:opacity-50">
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
}: {
  rows: MemoryRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  showStaleMeta?: "updated" | "accessed";
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-gray-500">No memories match.</p>;
  }
  return (
    <ul className="space-y-2">
      {rows.map((m) => (
        <li
          key={m.id}
          className={`border rounded p-3 flex gap-3 ${
            selected.has(m.id) ? "bg-blue-50 border-blue-300" : "hover:bg-gray-50"
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
              <span className="text-xs uppercase text-gray-500">{m.type}</span>
              <Link href={`/memories/${m.id}`} className="text-xs text-blue-600 hover:underline font-mono">
                {m.id.slice(0, 8)}
              </Link>
            </div>
            <p className="line-clamp-3 text-gray-900 text-sm">{m.content}</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {m.tags.map((t) => (
                <span key={t} className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">
                  {t}
                </span>
              ))}
            </div>
            {showStaleMeta && (
              <p className="text-xs text-gray-500 mt-1.5">
                {showStaleMeta === "updated" ? "Updated" : "Last read"}:{" "}
                {showStaleMeta === "updated"
                  ? formatDate(m.updated_at)
                  : m.last_accessed_at
                  ? formatDate(m.last_accessed_at)
                  : "never"}{" "}
                {showStaleMeta === "accessed" && m.access_count !== undefined && (
                  <span className="text-gray-400">· {m.access_count} reads</span>
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
