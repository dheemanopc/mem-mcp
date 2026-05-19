"use client";

import { useState } from "react";
import { Card } from "@/components/ui/Card";
import { MarkdownContent } from "@/components/ui/MarkdownContent";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MemoryRecord {
  id: string;
  content: string;
  type: string;
  tags: string[];
  version: number;
  parent_id: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

const MEMORY_TYPES = ["note", "decision", "fact", "snippet", "question"] as const;
type MemoryType = (typeof MEMORY_TYPES)[number];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

async function apiFetch<T>(
  url: string,
  init: RequestInit = {}
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  const res = await fetch(url, {
    credentials: "include",
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { ok: false, error: body.detail?.message ?? body.detail ?? `HTTP ${res.status}` };
  }
  const data = (await res.json()) as T;
  return { ok: true, data };
}

// ---------------------------------------------------------------------------
// ThreadView — top-level: root + nested replies
// ---------------------------------------------------------------------------

export function ThreadView({
  root,
  replies,
}: {
  root: MemoryRecord;
  replies: MemoryRecord[];
}) {
  // Single source of truth for the thread state — children mutate via callbacks.
  const [memories, setMemories] = useState<MemoryRecord[]>([root, ...replies]);

  const updateMemory = (id: string, patch: Partial<MemoryRecord>) =>
    setMemories((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m)));

  const addReply = (newReply: MemoryRecord) =>
    setMemories((ms) => [...ms, newReply]);

  const rootMem = memories.find((m) => m.id === root.id)!;
  const replyMems = memories.filter((m) => m.id !== root.id);

  return (
    <div className="space-y-4">
      <div id={rootMem.id}>
        <MemoryCard
          memory={rootMem}
          isRoot={true}
          onUpdate={(patch) => updateMemory(rootMem.id, patch)}
          onReplyCreated={addReply}
        />
      </div>
      {replyMems.length > 0 && (
        <div className="ml-6 border-l-2 border-border space-y-3 pl-4">
          {replyMems.map((reply) => (
            <div key={reply.id} id={reply.id}>
              <MemoryCard
                memory={reply}
                isRoot={false}
                onUpdate={(patch) => updateMemory(reply.id, patch)}
                onReplyCreated={addReply}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MemoryCard — one memory, with inline edit / reply / delete state
// ---------------------------------------------------------------------------

function MemoryCard({
  memory,
  isRoot,
  onUpdate,
  onReplyCreated,
}: {
  memory: MemoryRecord;
  isRoot: boolean;
  onUpdate: (patch: Partial<MemoryRecord>) => void;
  onReplyCreated: (reply: MemoryRecord) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [replying, setReplying] = useState(false);
  const isDeleted = !!memory.deleted_at;

  return (
    <Card
      variant={isDeleted ? "subtle" : "default"}
      pad="lg"
      className={isDeleted ? "opacity-60" : ""}
    >
      <div className="space-y-3">
        <CardHeader memory={memory} isRoot={isRoot} />
        {editing ? (
          <EditForm
            memory={memory}
            onCancel={() => setEditing(false)}
            onSaved={(patch) => {
              onUpdate(patch);
              setEditing(false);
            }}
          />
        ) : (
          <CardBody content={memory.content} />
        )}
        <CardFooter
          memory={memory}
          isDeleted={isDeleted}
          editing={editing}
          replying={replying}
          onToggleEdit={() => setEditing((e) => !e)}
          onToggleReply={() => setReplying((r) => !r)}
          onDelete={async () => {
            if (!confirm("Soft-delete this memory? Recoverable for 30 days.")) return;
            const r = await apiFetch<{ id: string; deleted_at: string }>(
              `/api/web/memories/${memory.id}`,
              { method: "DELETE" }
            );
            if (r.ok) onUpdate({ deleted_at: r.data.deleted_at });
            else alert(r.error);
          }}
          onUndelete={async () => {
            const r = await apiFetch<unknown>(
              `/api/web/memories/${memory.id}/undelete`,
              { method: "POST" }
            );
            if (r.ok) onUpdate({ deleted_at: null });
            else alert(r.error);
          }}
        />
        {replying && (
          <ReplyComposer
            parentId={isRoot ? memory.id : memory.parent_id ?? memory.id}
            parentTags={memory.tags}
            onCancel={() => setReplying(false)}
            onCreated={(reply) => {
              onReplyCreated(reply);
              setReplying(false);
            }}
          />
        )}
      </div>
    </Card>
  );
}

function CardHeader({ memory, isRoot }: { memory: MemoryRecord; isRoot: boolean }) {
  return (
    <div className="flex justify-between items-start text-xs">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="uppercase text-ink-muted font-medium">{memory.type}</span>
        {isRoot && memory.version > 1 && (
          <span className="text-ink-faint">v{memory.version}</span>
        )}
        {memory.deleted_at && (
          <span className="text-danger font-medium">deleted</span>
        )}
      </div>
      <code className="text-ink-faint font-mono">{memory.id.slice(0, 8)}</code>
    </div>
  );
}

function CardBody({ content }: { content: string }) {
  return <MarkdownContent content={content} variant="full" />;
}

function CardFooter({
  memory,
  isDeleted,
  editing,
  replying,
  onToggleEdit,
  onToggleReply,
  onDelete,
  onUndelete,
}: {
  memory: MemoryRecord;
  isDeleted: boolean;
  editing: boolean;
  replying: boolean;
  onToggleEdit: () => void;
  onToggleReply: () => void;
  onDelete: () => void | Promise<void>;
  onUndelete: () => void | Promise<void>;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-border text-xs">
      <div className="flex flex-wrap gap-1">
        {memory.tags.map((t) => (
          <span key={t} className="bg-surface-subtle px-2 py-0.5 rounded text-ink-muted">
            {t}
          </span>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-ink-faint" title={`created ${fmt(memory.created_at)}`}>
          {fmt(memory.updated_at)}
        </span>
        {!editing && !isDeleted && (
          <button onClick={onToggleEdit} className="text-primary hover:underline">
            edit
          </button>
        )}
        {!isDeleted && (
          <button
            onClick={onToggleReply}
            className="text-primary hover:underline"
          >
            {replying ? "cancel reply" : "reply"}
          </button>
        )}
        {!isDeleted ? (
          <button onClick={onDelete} className="text-danger hover:underline">
            delete
          </button>
        ) : (
          <button onClick={onUndelete} className="text-primary hover:underline">
            restore
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline edit
// ---------------------------------------------------------------------------

function EditForm({
  memory,
  onCancel,
  onSaved,
}: {
  memory: MemoryRecord;
  onCancel: () => void;
  onSaved: (patch: Partial<MemoryRecord>) => void;
}) {
  const [content, setContent] = useState(memory.content);
  const [tagsText, setTagsText] = useState(memory.tags.join(", "));
  const [type, setType] = useState<MemoryType>(memory.type as MemoryType);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    const tags = tagsText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const r = await apiFetch<{ id: string; tags: string[] | null }>(
      `/api/web/memories/${memory.id}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          content,
          tags,
          tags_op: "replace",
          type,
        }),
      }
    );
    if (r.ok) {
      onSaved({
        content,
        tags: r.data.tags ?? tags,
        type,
        updated_at: new Date().toISOString(),
      });
    } else {
      setError(r.error);
    }
    setBusy(false);
  };

  return (
    <div className="space-y-2">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={Math.min(20, Math.max(4, content.split("\n").length + 1))}
        className="w-full rounded border border-border-strong p-2 text-[15px] text-ink font-mono leading-relaxed focus:border-border-focus focus:outline-none"
      />
      <div className="flex flex-wrap gap-2 items-center">
        <select
          value={type}
          onChange={(e) => setType(e.target.value as MemoryType)}
          className="text-xs rounded border border-border-strong px-2 py-1 bg-surface"
        >
          {MEMORY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          value={tagsText}
          onChange={(e) => setTagsText(e.target.value)}
          placeholder="comma,separated,tags"
          className="text-xs rounded border border-border-strong px-2 py-1 flex-1 min-w-[200px] focus:border-border-focus focus:outline-none"
        />
      </div>
      {error && <p className="text-danger text-xs">{error}</p>}
      <div className="flex gap-2 text-xs">
        <button
          onClick={save}
          disabled={busy}
          className="bg-primary text-ink-inverse rounded px-3 py-1 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button
          onClick={onCancel}
          disabled={busy}
          className="rounded px-3 py-1 border border-border text-ink"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline reply composer
// ---------------------------------------------------------------------------

function ReplyComposer({
  parentId,
  parentTags,
  onCancel,
  onCreated,
}: {
  parentId: string;
  parentTags: string[];
  onCancel: () => void;
  onCreated: (reply: MemoryRecord) => void;
}) {
  const [content, setContent] = useState("");
  const [type, setType] = useState<MemoryType>("note");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!content.trim()) {
      setError("content cannot be empty");
      return;
    }
    setBusy(true);
    setError(null);
    const r = await apiFetch<{ id: string; created_at: string }>(
      `/api/web/memories`,
      {
        method: "POST",
        body: JSON.stringify({
          content,
          type,
          parent_id: parentId,
          tags: [],
        }),
      }
    );
    if (r.ok) {
      onCreated({
        id: r.data.id,
        content,
        type,
        tags: parentTags, // replies inherit parent tags server-side
        version: 1,
        parent_id: parentId,
        created_at: r.data.created_at,
        updated_at: r.data.created_at,
        deleted_at: null,
      });
      setContent("");
    } else {
      setError(r.error);
    }
    setBusy(false);
  };

  return (
    <div className="mt-2 rounded border border-border-strong bg-surface-muted p-3 space-y-2">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="Write a reply…"
        rows={4}
        className="w-full rounded border border-border p-2 text-[15px] text-ink focus:border-border-focus focus:outline-none"
      />
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <select
          value={type}
          onChange={(e) => setType(e.target.value as MemoryType)}
          className="rounded border border-border-strong px-2 py-1 bg-surface"
        >
          {MEMORY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="text-ink-faint">replies inherit parent tags automatically</span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={submit}
            disabled={busy}
            className="bg-primary text-ink-inverse rounded px-3 py-1 disabled:opacity-50"
          >
            {busy ? "Posting…" : "Post reply"}
          </button>
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded px-3 py-1 border border-border text-ink"
          >
            Cancel
          </button>
        </div>
      </div>
      {error && <p className="text-danger text-xs">{error}</p>}
    </div>
  );
}
