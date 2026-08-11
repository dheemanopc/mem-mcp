"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { csrfHeaders } from "@/lib/csrf";

export interface MapNode {
  memory_id: string;
  node_role: string;
  type: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  status: string;
  turn: string | null;
  decision_criteria: string | null;
  truncated: boolean;
}

export interface ResolvedBranch {
  memory_id: string;
  resolution_summary: string | null;
  status: string;
  resolved_by_party: string | null;
}

export interface MapEdge {
  source_memory_id: string;
  target_memory_id: string;
  reference_kind: string;
}

export interface MapPayload {
  map_key: string;
  root_memory_id: string;
  title: string;
  state: string;
  cursor: number;
  nodes: MapNode[];
  resolved: ResolvedBranch[];
  edges: MapEdge[];
  open_loops: string[];
}

/** Edge verbs carry the argument structure — that semantics IS the payload. */
const EDGE_LABEL: Record<string, string> = {
  "displaced-from": "challenges",
  "resolves-under": "resolves",
  "dropped-under": "drops",
  "principle-under": "constrains",
  "superseded-under": "supersedes",
  "open-under": "opens under",
  answers: "answers",
};

const ROLE_STYLE: Record<string, string> = {
  root: "border-slate-400 bg-slate-50 dark:border-slate-600 dark:bg-slate-900/60",
  question: "border-amber-300 bg-amber-50/70 dark:border-amber-800 dark:bg-amber-950/25",
  challenge: "border-rose-200 bg-rose-50/60 dark:border-rose-900 dark:bg-rose-950/20",
  position: "border-sky-200 bg-sky-50/50 dark:border-sky-900 dark:bg-sky-950/20",
  note: "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950/40",
};

interface Threaded {
  node: MapNode;
  depth: number;
  /** Edges beyond the primary parent — rendered as chips so the DAG survives. */
  extraEdges: { kind: string; target: string }[];
}

/**
 * Build a threaded outline from a DAG.
 *
 * A node with two parents cannot appear once in a strict tree, so each node
 * renders under its FIRST parent (deterministic) and every additional edge
 * becomes an inline chip. That keeps the graph's cross-links visible without
 * duplicating nodes or reaching for a canvas.
 */
function threadNodes(nodes: MapNode[], edges: MapEdge[], rootId: string): Threaded[] {
  const byId = new Map(nodes.map((n) => [n.memory_id, n]));
  const primaryParent = new Map<string, string>();
  const extra = new Map<string, { kind: string; target: string }[]>();

  for (const e of edges) {
    if (!byId.has(e.source_memory_id) || !byId.has(e.target_memory_id)) continue;
    if (!primaryParent.has(e.source_memory_id)) {
      primaryParent.set(e.source_memory_id, e.target_memory_id);
    } else {
      const list = extra.get(e.source_memory_id) ?? [];
      list.push({ kind: e.reference_kind, target: e.target_memory_id });
      extra.set(e.source_memory_id, list);
    }
  }

  const children = new Map<string, string[]>();
  const orphans: string[] = [];
  for (const n of nodes) {
    if (n.memory_id === rootId) continue;
    const parent = primaryParent.get(n.memory_id);
    if (parent) {
      const list = children.get(parent) ?? [];
      list.push(n.memory_id);
      children.set(parent, list);
    } else {
      orphans.push(n.memory_id);
    }
  }

  const out: Threaded[] = [];
  const seen = new Set<string>();
  const walk = (id: string, depth: number) => {
    if (seen.has(id)) return; // cycle guard — edges are free text, trust nothing
    seen.add(id);
    const node = byId.get(id);
    if (!node) return;
    out.push({ node, depth, extraEdges: extra.get(id) ?? [] });
    for (const child of children.get(id) ?? []) walk(child, depth + 1);
  };

  walk(rootId, 0);
  // Unattached nodes still belong to the map; hiding them would quietly lose
  // content, so they trail the tree at depth 1 rather than vanishing.
  for (const id of orphans) walk(id, 1);
  return out;
}

export function MapView({ mapKey, data }: { mapKey: string; data: MapPayload }) {
  const router = useRouter();
  const [answering, setAnswering] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [endorse, setEndorse] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isArchived = data.state !== "live";
  const threaded = useMemo(
    () => threadNodes(data.nodes, data.edges, data.root_memory_id),
    [data.nodes, data.edges, data.root_memory_id],
  );
  const openLoops = new Set(data.open_loops);
  const resolvedById = new Map(data.resolved.map((r) => [r.memory_id, r]));

  async function submitAnswer(questionId: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/web/mindmaps/${encodeURIComponent(mapKey)}/answer`, {
        method: "POST",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          question_node_id: questionId,
          content: draft,
          answered_by: "owner",
          ratification_strength: endorse ? "explicit-endorse" : null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.detail?.message ?? `Failed (${res.status})`);
        return;
      }
      setAnswering(null);
      setDraft("");
      setEndorse(false);
      router.refresh();
    } catch {
      setError("Network error — nothing was written.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">
            {data.title}
          </h1>
          {isArchived && (
            <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              archived · read-only
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {data.nodes.length} nodes · {data.resolved.length} settled · cursor {data.cursor}
        </p>
        {isArchived && (
          <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
            Graduated maps are no-reopen by design. You can read the reasoning, but
            not add to it.
          </p>
        )}
      </header>

      {error && (
        <p className="mb-4 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
          {error}
        </p>
      )}

      <ol className="space-y-2">
        {threaded.map(({ node, depth, extraEdges }) => {
          const settled = resolvedById.get(node.memory_id);
          const isOpenLoop = openLoops.has(node.memory_id);
          return (
            <li
              key={node.memory_id}
              id={node.memory_id}
              style={{ marginLeft: `${Math.min(depth, 6) * 1.25}rem` }}
            >
              <div
                className={`rounded-lg border p-3 ${
                  ROLE_STYLE[node.node_role] ?? ROLE_STYLE.note
                } ${settled ? "opacity-60" : ""}`}
              >
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    {node.node_role}
                  </span>
                  {settled && (
                    <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      {settled.status}
                    </span>
                  )}
                  {isOpenLoop && (
                    <span className="rounded bg-amber-200 px-1.5 py-0.5 font-medium text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">
                      awaiting you
                    </span>
                  )}
                  {extraEdges.map((e, i) => (
                    <a
                      key={i}
                      href={`#${e.target}`}
                      className="rounded border border-slate-300 px-1.5 py-0.5 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                      title={e.kind}
                    >
                      also {EDGE_LABEL[e.kind] ?? e.kind} ↗
                    </a>
                  ))}
                </div>

                <p className="whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
                  {settled?.resolution_summary ?? node.content}
                </p>

                {node.decision_criteria && !settled && (
                  <p className="mt-2 border-l-2 border-amber-300 pl-3 text-xs text-slate-600 dark:border-amber-700 dark:text-slate-400">
                    <span className="font-medium">What turns on this:</span>{" "}
                    {node.decision_criteria}
                  </p>
                )}

                {isOpenLoop && !isArchived && !settled && (
                  <div className="mt-3">
                    {answering === node.memory_id ? (
                      <div className="space-y-2">
                        <textarea
                          className="w-full rounded border border-slate-300 bg-white p-2 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                          rows={4}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          placeholder="Your answer…"
                          autoFocus
                        />
                        <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
                          <input
                            type="checkbox"
                            checked={endorse}
                            onChange={(e) => setEndorse(e.target.checked)}
                          />
                          Record this as an explicit endorsement (your words, cited)
                        </label>
                        <div className="flex gap-2">
                          <button
                            onClick={() => submitAnswer(node.memory_id)}
                            disabled={busy || draft.trim().length === 0}
                            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
                          >
                            {busy ? "Saving…" : "Answer & resolve"}
                          </button>
                          <button
                            onClick={() => {
                              setAnswering(null);
                              setDraft("");
                              setError(null);
                            }}
                            disabled={busy}
                            className="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setAnswering(node.memory_id);
                          setError(null);
                        }}
                        className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-white dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        Answer
                      </button>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
