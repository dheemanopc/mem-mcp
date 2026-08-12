"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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

const ROLE_CARD: Record<string, string> = {
  root: "border-slate-400 bg-slate-100 dark:border-slate-500 dark:bg-slate-800",
  question: "border-amber-400 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/60",
  challenge: "border-rose-300 bg-rose-50 dark:border-rose-800 dark:bg-rose-950/50",
  position: "border-sky-300 bg-sky-50 dark:border-sky-800 dark:bg-sky-950/50",
  note: "border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900",
};

const LEGEND: [string, string][] = [
  ["root", "bg-slate-400 dark:bg-slate-500"],
  ["question", "bg-amber-400 dark:bg-amber-600"],
  ["challenge", "bg-rose-400 dark:bg-rose-700"],
  ["position", "bg-sky-400 dark:bg-sky-700"],
  ["note", "bg-slate-300 dark:bg-slate-600"],
];

/* Canvas geometry. Left-to-right layout: depth → x, siblings stack in y. */
const NODE_W = 224;
const NODE_H = 60;
const COL_W = 296;
const ROW_H = 84;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 2.5;

interface CrossEdge {
  kind: string;
  other: string;
}

interface Graph {
  byId: Map<string, MapNode>;
  /** Primary parent (first edge wins — deterministic, matches the old outline). */
  parentOf: Map<string, string>;
  /** Verb of the primary edge to the parent. */
  parentKind: Map<string, string>;
  childrenOf: Map<string, string[]>;
  crossOut: Map<string, CrossEdge[]>;
  crossIn: Map<string, CrossEdge[]>;
  /** Map root first, then unattached nodes — hiding orphans would lose content. */
  roots: string[];
  depthOf: Map<string, number>;
  /** Descendant count per node, respecting the same cycle guard as the walk. */
  descCount: Map<string, number>;
}

/**
 * Build a tree-shaped view of the DAG. A node with two parents hangs under
 * its FIRST parent; every additional edge becomes a cross-link drawn as a
 * dashed curve. Cycle-guarded throughout — reference_kind is unvalidated
 * free text and nothing about the edge set can be trusted.
 */
function buildGraph(nodes: MapNode[], edges: MapEdge[], rootId: string): Graph {
  const byId = new Map(nodes.map((n) => [n.memory_id, n]));
  const parentOf = new Map<string, string>();
  const parentKind = new Map<string, string>();
  const crossOut = new Map<string, CrossEdge[]>();
  const crossIn = new Map<string, CrossEdge[]>();

  for (const e of edges) {
    if (!byId.has(e.source_memory_id) || !byId.has(e.target_memory_id)) continue;
    if (!parentOf.has(e.source_memory_id) && e.source_memory_id !== rootId) {
      parentOf.set(e.source_memory_id, e.target_memory_id);
      parentKind.set(e.source_memory_id, e.reference_kind);
    } else {
      const out = crossOut.get(e.source_memory_id) ?? [];
      out.push({ kind: e.reference_kind, other: e.target_memory_id });
      crossOut.set(e.source_memory_id, out);
      const inn = crossIn.get(e.target_memory_id) ?? [];
      inn.push({ kind: e.reference_kind, other: e.source_memory_id });
      crossIn.set(e.target_memory_id, inn);
    }
  }

  const childrenOf = new Map<string, string[]>();
  const roots: string[] = [rootId];
  for (const n of nodes) {
    if (n.memory_id === rootId) continue;
    const parent = parentOf.get(n.memory_id);
    if (parent) {
      const list = childrenOf.get(parent) ?? [];
      list.push(n.memory_id);
      childrenOf.set(parent, list);
    } else {
      roots.push(n.memory_id);
    }
  }

  const depthOf = new Map<string, number>();
  const descCount = new Map<string, number>();
  const seen = new Set<string>();
  const measure = (id: string, depth: number): number => {
    if (seen.has(id)) return -1;
    seen.add(id);
    depthOf.set(id, depth);
    let count = 0;
    for (const child of childrenOf.get(id) ?? []) {
      const sub = measure(child, depth + 1);
      if (sub >= 0) count += 1 + sub;
    }
    descCount.set(id, count);
    return count;
  };
  for (const r of roots) measure(r, 0);

  return {
    byId,
    parentOf,
    parentKind,
    childrenOf,
    crossOut,
    crossIn,
    roots,
    depthOf,
    descCount,
  };
}

/**
 * Big maps open at two levels deep so the shape is readable at a glance;
 * branches with a question awaiting the owner are always revealed — the
 * whole point of the page is that those never go unnoticed.
 */
function defaultCollapsed(graph: Graph, openLoops: string[]): Set<string> {
  const collapsed = new Set<string>();
  for (const [id, depth] of graph.depthOf) {
    if (depth >= 2 && (graph.childrenOf.get(id)?.length ?? 0) > 0) {
      collapsed.add(id);
    }
  }
  for (const loop of openLoops) {
    let cur = graph.parentOf.get(loop);
    const guard = new Set<string>();
    while (cur && !guard.has(cur)) {
      guard.add(cur);
      collapsed.delete(cur);
      cur = graph.parentOf.get(cur);
    }
  }
  return collapsed;
}

interface Layout {
  pos: Map<string, { x: number; y: number }>;
  visible: string[];
  bounds: { w: number; h: number };
}

function computeLayout(graph: Graph, collapsed: Set<string>): Layout {
  const pos = new Map<string, { x: number; y: number }>();
  const visible: string[] = [];
  const seen = new Set<string>();
  let leaf = 0;

  const place = (id: string, depth: number): number => {
    if (seen.has(id)) return -1;
    seen.add(id);
    visible.push(id);
    const kids = collapsed.has(id) ? [] : (graph.childrenOf.get(id) ?? []);
    const ys: number[] = [];
    for (const k of kids) {
      const y = place(k, depth + 1);
      if (y >= 0) ys.push(y);
    }
    const y = ys.length > 0 ? (ys[0] + ys[ys.length - 1]) / 2 : leaf++ * ROW_H;
    pos.set(id, { x: depth * COL_W, y });
    return y;
  };
  for (const r of graph.roots) place(r, 0);

  let maxX = 0;
  for (const p of pos.values()) maxX = Math.max(maxX, p.x);
  return {
    pos,
    visible,
    bounds: { w: maxX + NODE_W, h: Math.max(leaf - 1, 0) * ROW_H + NODE_H },
  };
}

function edgePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const x1 = from.x + NODE_W;
  const y1 = from.y + NODE_H / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_H / 2;
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

function crossPath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const x1 = from.x + NODE_W / 2;
  const y1 = from.y + NODE_H;
  const x2 = to.x + NODE_W / 2;
  const y2 = to.y + NODE_H;
  const lift = Math.min(120, Math.abs(x2 - x1) * 0.3 + 48);
  return `M ${x1} ${y1} C ${x1} ${y1 + lift}, ${x2} ${y2 + lift}, ${x2} ${y2}`;
}

export function GraphView({ mapKey, data }: { mapKey: string; data: MapPayload }) {
  const router = useRouter();
  const isArchived = data.state !== "live";
  const openLoops = useMemo(() => new Set(data.open_loops), [data.open_loops]);
  const resolvedById = useMemo(
    () => new Map(data.resolved.map((r) => [r.memory_id, r])),
    [data.resolved],
  );

  const graph = useMemo(
    () => buildGraph(data.nodes, data.edges, data.root_memory_id),
    [data.nodes, data.edges, data.root_memory_id],
  );

  const [collapsed, setCollapsed] = useState<Set<string>>(() =>
    defaultCollapsed(graph, data.open_loops),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [transform, setTransform] = useState({ x: 40, y: 40, k: 1 });

  const [answering, setAnswering] = useState(false);
  const [draft, setDraft] = useState("");
  const [endorse, setEndorse] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const layout = useMemo(() => computeLayout(graph, collapsed), [graph, collapsed]);

  const wrapRef = useRef<HTMLDivElement>(null);
  const panRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const pendingCenter = useRef<string | null>(null);
  const didInit = useRef(false);

  function fitView() {
    const el = wrapRef.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const pad = 48;
    const k = Math.min(
      1,
      (width - pad * 2) / Math.max(layout.bounds.w, 1),
      (height - pad * 2) / Math.max(layout.bounds.h, 1),
    );
    setTransform({
      x: (width - layout.bounds.w * k) / 2,
      y: (height - layout.bounds.h * k) / 2,
      k: Math.max(k, MIN_ZOOM),
    });
  }

  function centerOn(id: string) {
    const el = wrapRef.current;
    const p = layout.pos.get(id);
    if (!el || !p) return;
    const { width, height } = el.getBoundingClientRect();
    setTransform((t) => ({
      ...t,
      x: width * 0.4 - (p.x + NODE_W / 2) * t.k,
      y: height / 2 - (p.y + NODE_H / 2) * t.k,
    }));
  }

  /** Expand every ancestor so the node is actually on canvas, then select it. */
  function revealAndSelect(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      let cur = graph.parentOf.get(id);
      const guard = new Set<string>();
      while (cur && !guard.has(cur)) {
        guard.add(cur);
        next.delete(cur);
        cur = graph.parentOf.get(cur);
      }
      return next;
    });
    setSelected(id);
    setAnswering(false);
    setError(null);
    pendingCenter.current = id;
  }

  // Centering must wait for the layout that includes the revealed node.
  useEffect(() => {
    if (pendingCenter.current && layout.pos.has(pendingCenter.current)) {
      centerOn(pendingCenter.current);
      pendingCenter.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout]);

  // Initial view: honor a #node-id deep link (the queue page links here),
  // otherwise fit the whole map.
  useLayoutEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    const hash = window.location.hash.slice(1);
    if (hash && graph.byId.has(hash)) {
      revealAndSelect(hash);
    } else {
      fitView();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React attaches wheel listeners passively, so zoom needs a native
  // non-passive listener to be able to preventDefault page scroll.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      setTransform((t) => {
        const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, t.k * Math.exp(-e.deltaY * 0.0015)));
        return {
          k,
          x: px - ((px - t.x) / t.k) * k,
          y: py - ((py - t.y) / t.k) * k,
        };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function zoomBy(factor: number) {
    const el = wrapRef.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setTransform((t) => {
      const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, t.k * factor));
      return {
        k,
        x: width / 2 - ((width / 2 - t.x) / t.k) * k,
        y: height / 2 - ((height / 2 - t.y) / t.k) * k,
      };
    });
  }

  function toggleBranch(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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
      setAnswering(false);
      setDraft("");
      setEndorse(false);
      router.refresh();
    } catch {
      setError("Network error — nothing was written.");
    } finally {
      setBusy(false);
    }
  }

  /** Path root → node following primary parents; the selected node's trace. */
  const trace = useMemo(() => {
    if (!selected) return [];
    const path: string[] = [];
    const guard = new Set<string>();
    let cur: string | undefined = selected;
    while (cur && !guard.has(cur)) {
      guard.add(cur);
      path.unshift(cur);
      cur = graph.parentOf.get(cur);
    }
    return path;
  }, [selected, graph]);

  const selectedNode = selected ? graph.byId.get(selected) : undefined;
  const selectedSettled = selected ? resolvedById.get(selected) : undefined;
  const selectedIsOpenLoop = selected !== null && openLoops.has(selected);

  const snippet = (id: string, len = 80) => {
    const text = graph.byId.get(id)?.content ?? id;
    return text.length > len ? `${text.slice(0, len)}…` : text;
  };

  return (
    <div>
      <header className="mb-4">
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
          {data.nodes.length} nodes · {data.resolved.length} settled ·{" "}
          {data.open_loops.length} awaiting you · cursor {data.cursor}
        </p>
        {isArchived && (
          <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
            Graduated maps are no-reopen by design. You can read the reasoning, but
            not add to it.
          </p>
        )}
      </header>

      <div className="flex flex-col gap-4 lg:flex-row">
        {/* ── Diagram (LHS) ─────────────────────────────────────────────── */}
        <div
          ref={wrapRef}
          className="relative h-[60vh] min-w-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950 lg:h-[calc(100vh-14rem)]"
        >
          <svg
            className="h-full w-full touch-none select-none"
            onPointerDown={(e) => {
              if (e.target instanceof Element && e.target.closest("[data-node]")) return;
              (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
              panRef.current = {
                x: e.clientX,
                y: e.clientY,
                tx: transform.x,
                ty: transform.y,
              };
            }}
            onPointerMove={(e) => {
              const pan = panRef.current;
              if (!pan) return;
              setTransform((t) => ({
                ...t,
                x: pan.tx + (e.clientX - pan.x),
                y: pan.ty + (e.clientY - pan.y),
              }));
            }}
            onPointerUp={() => {
              panRef.current = null;
            }}
            role="img"
            aria-label={`Mind map graph for ${data.title}`}
          >
            <defs>
              <pattern id="dotgrid" width="20" height="20" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="1" className="fill-slate-300/60 dark:fill-slate-700/60" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#dotgrid)" className="cursor-grab" />

            <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
              {/* Primary edges: parent → child */}
              {layout.visible.map((id) => {
                const parent = graph.parentOf.get(id);
                if (!parent) return null;
                const from = layout.pos.get(parent);
                const to = layout.pos.get(id);
                if (!from || !to) return null;
                return (
                  <path
                    key={`e-${id}`}
                    d={edgePath(from, to)}
                    fill="none"
                    strokeWidth={1.5}
                    className="stroke-slate-300 dark:stroke-slate-700"
                  >
                    <title>{EDGE_LABEL[graph.parentKind.get(id) ?? ""] ?? graph.parentKind.get(id)}</title>
                  </path>
                );
              })}

              {/* Cross-links: the DAG edges beyond each node's primary parent */}
              {layout.visible.map((id) =>
                (graph.crossOut.get(id) ?? []).map((edge, i) => {
                  const from = layout.pos.get(id);
                  const to = layout.pos.get(edge.other);
                  if (!from || !to) return null;
                  return (
                    <path
                      key={`x-${id}-${i}`}
                      d={crossPath(from, to)}
                      fill="none"
                      strokeWidth={1.25}
                      strokeDasharray="5 4"
                      className="stroke-violet-400/80 dark:stroke-violet-500/70"
                    >
                      <title>{`${EDGE_LABEL[edge.kind] ?? edge.kind} → ${snippet(edge.other, 60)}`}</title>
                    </path>
                  );
                }),
              )}

              {/* Nodes */}
              {layout.visible.map((id) => {
                const node = graph.byId.get(id);
                const p = layout.pos.get(id);
                if (!node || !p) return null;
                const settled = resolvedById.get(id);
                const isLoop = openLoops.has(id);
                const childCount = graph.childrenOf.get(id)?.length ?? 0;
                const isCollapsed = collapsed.has(id);
                const hidden = graph.descCount.get(id) ?? 0;
                return (
                  <foreignObject
                    key={id}
                    x={p.x}
                    y={p.y}
                    width={NODE_W}
                    height={NODE_H}
                    data-node={id}
                  >
                    <div
                      onClick={() => {
                        if (isCollapsed) toggleBranch(id);
                        setSelected(id);
                        setAnswering(false);
                        setError(null);
                      }}
                      className={`flex h-full cursor-pointer items-stretch overflow-hidden rounded-lg border shadow-sm transition-shadow hover:shadow-md ${
                        ROLE_CARD[node.node_role] ?? ROLE_CARD.note
                      } ${settled ? "opacity-60" : ""} ${
                        selected === id
                          ? "ring-2 ring-blue-500 dark:ring-blue-400"
                          : isLoop
                            ? "ring-2 ring-amber-400/80 dark:ring-amber-500/70"
                            : ""
                      }`}
                    >
                      <div className="min-w-0 flex-1 px-2.5 py-1.5">
                        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                          <span className="truncate">{node.node_role}</span>
                          {isLoop && (
                            <span className="shrink-0 rounded bg-amber-300 px-1 normal-case text-amber-900 dark:bg-amber-700 dark:text-amber-100">
                              ?
                            </span>
                          )}
                          {settled && (
                            <span className="shrink-0 rounded bg-slate-200 px-1 normal-case text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                              {settled.status}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-slate-800 dark:text-slate-200">
                          {node.content}
                        </p>
                      </div>
                      {childCount > 0 && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleBranch(id);
                          }}
                          title={isCollapsed ? `Expand ${hidden} hidden` : "Collapse branch"}
                          className="flex w-7 shrink-0 items-center justify-center border-l border-inherit bg-white/50 text-[11px] font-semibold text-slate-600 hover:bg-white dark:bg-slate-900/40 dark:text-slate-300 dark:hover:bg-slate-800"
                        >
                          {isCollapsed ? `+${hidden}` : "−"}
                        </button>
                      )}
                    </div>
                  </foreignObject>
                );
              })}
            </g>
          </svg>

          {/* Toolbar */}
          <div className="absolute left-3 top-3 flex gap-1 rounded-lg border border-slate-200 bg-white/90 p-1 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/90">
            <button
              onClick={() => zoomBy(1.25)}
              title="Zoom in"
              className="rounded px-2 py-1 text-sm text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              +
            </button>
            <button
              onClick={() => zoomBy(0.8)}
              title="Zoom out"
              className="rounded px-2 py-1 text-sm text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              −
            </button>
            <button
              onClick={fitView}
              title="Fit map to view"
              className="rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Fit
            </button>
            <button
              onClick={() => setCollapsed(new Set())}
              title="Expand every branch"
              className="rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Expand all
            </button>
            <button
              onClick={() => setCollapsed(defaultCollapsed(graph, data.open_loops))}
              title="Back to the default two-level view"
              className="rounded px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Reset
            </button>
          </div>

          {/* Legend */}
          <div className="absolute bottom-3 left-3 hidden items-center gap-3 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-1.5 text-[11px] text-slate-600 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/90 dark:text-slate-400 sm:flex">
            {LEGEND.map(([role, dot]) => (
              <span key={role} className="flex items-center gap-1">
                <span className={`h-2 w-2 rounded-full ${dot}`} />
                {role}
              </span>
            ))}
            <span className="flex items-center gap-1">
              <span className="h-0 w-4 border-t border-dashed border-violet-400" />
              cross-link
            </span>
          </div>
        </div>

        {/* ── Detail panel (RHS) ────────────────────────────────────────── */}
        <aside className="w-full shrink-0 overflow-y-auto rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950 lg:h-[calc(100vh-14rem)] lg:w-[380px]">
          {error && (
            <p className="mb-3 rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
              {error}
            </p>
          )}

          {!selectedNode ? (
            <div>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Click a node in the diagram to read it here — its full text plus the
                trace of how the map reached it.
              </p>
              {data.open_loops.length > 0 && !isArchived && (
                <div className="mt-5">
                  <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Waiting on you
                  </h2>
                  <ul className="space-y-2">
                    {data.open_loops.map((id) =>
                      graph.byId.has(id) ? (
                        <li key={id}>
                          <button
                            onClick={() => revealAndSelect(id)}
                            className="w-full rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-left text-sm text-slate-800 hover:bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-slate-200 dark:hover:bg-amber-950/40"
                          >
                            {snippet(id, 120)}
                          </button>
                        </li>
                      ) : null,
                    )}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div>
              {/* Node trace: the primary-parent path from the root */}
              <nav aria-label="Node trace" className="mb-4">
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Trace
                </h2>
                <ol className="space-y-1">
                  {trace.map((id, i) => (
                    <li key={id} style={{ marginLeft: `${Math.min(i, 5) * 0.75}rem` }}>
                      {i > 0 && (
                        <span className="mr-1 text-[11px] text-slate-400 dark:text-slate-500">
                          ↳ {EDGE_LABEL[graph.parentKind.get(id) ?? ""] ?? graph.parentKind.get(id) ?? ""}
                        </span>
                      )}
                      <button
                        onClick={() => revealAndSelect(id)}
                        className={`text-left text-xs hover:underline ${
                          id === selected
                            ? "font-semibold text-slate-900 dark:text-slate-100"
                            : "text-slate-600 dark:text-slate-400"
                        }`}
                      >
                        {snippet(id, 64)}
                      </button>
                    </li>
                  ))}
                </ol>
              </nav>

              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  {selectedNode.node_role}
                </span>
                {selectedSettled && (
                  <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                    {selectedSettled.status}
                    {selectedSettled.resolved_by_party
                      ? ` · by ${selectedSettled.resolved_by_party}`
                      : ""}
                  </span>
                )}
                {selectedIsOpenLoop && (
                  <span className="rounded bg-amber-200 px-1.5 py-0.5 font-medium text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">
                    awaiting you
                  </span>
                )}
              </div>

              <p className="whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-200">
                {selectedNode.content}
              </p>

              {selectedSettled?.resolution_summary && (
                <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/40">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Resolution
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-300">
                    {selectedSettled.resolution_summary}
                  </p>
                </div>
              )}

              {selectedNode.decision_criteria && !selectedSettled && (
                <p className="mt-3 border-l-2 border-amber-300 pl-3 text-xs text-slate-600 dark:border-amber-700 dark:text-slate-400">
                  <span className="font-medium">What turns on this:</span>{" "}
                  {selectedNode.decision_criteria}
                </p>
              )}

              {/* Every edge touching this node, with its verb — the DAG's
                  cross-links live here even when their curves are off-screen. */}
              {((graph.crossOut.get(selected!) ?? []).length > 0 ||
                (graph.crossIn.get(selected!) ?? []).length > 0) && (
                <div className="mt-4">
                  <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Cross-links
                  </h2>
                  <ul className="space-y-1.5 text-xs">
                    {(graph.crossOut.get(selected!) ?? []).map((e, i) => (
                      <li key={`o${i}`}>
                        <span className="text-slate-500 dark:text-slate-400">
                          also {EDGE_LABEL[e.kind] ?? e.kind} →{" "}
                        </span>
                        <button
                          onClick={() => revealAndSelect(e.other)}
                          className="text-left text-slate-700 hover:underline dark:text-slate-300"
                        >
                          {snippet(e.other, 70)}
                        </button>
                      </li>
                    ))}
                    {(graph.crossIn.get(selected!) ?? []).map((e, i) => (
                      <li key={`i${i}`}>
                        <button
                          onClick={() => revealAndSelect(e.other)}
                          className="text-left text-slate-700 hover:underline dark:text-slate-300"
                        >
                          {snippet(e.other, 70)}
                        </button>{" "}
                        <span className="text-slate-500 dark:text-slate-400">
                          {EDGE_LABEL[e.kind] ?? e.kind} this
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selectedIsOpenLoop && !isArchived && !selectedSettled && (
                <div className="mt-4">
                  {answering ? (
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
                          onClick={() => submitAnswer(selected!)}
                          disabled={busy || draft.trim().length === 0}
                          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
                        >
                          {busy ? "Saving…" : "Answer & resolve"}
                        </button>
                        <button
                          onClick={() => {
                            setAnswering(false);
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
                        setAnswering(true);
                        setError(null);
                      }}
                      className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      Answer
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
