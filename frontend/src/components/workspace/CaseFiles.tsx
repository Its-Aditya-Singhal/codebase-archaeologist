"use client";

import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Check, FolderOpen, Loader2, Pencil, Trash2, X } from "lucide-react";
import { api, type InvestigationSummary } from "@/lib/api";
import { EASE } from "@/components/motion/primitives";

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** Saved investigations for this repository: reopen, rename, delete. */
export function CaseFiles({
  repoId,
  currentId,
  onClose,
  onOpen,
  onRenamed,
  onDeleted,
}: {
  repoId: number;
  currentId: number | null;
  onClose: () => void;
  onOpen: (id: number) => void;
  onRenamed: (id: number, title: string) => void;
  onDeleted: (id: number) => void;
}) {
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: number; title: string } | null>(null);

  useEffect(() => {
    api
      .investigations(repoId)
      .then(setItems)
      .catch((e) => setError((e as Error).message));
  }, [repoId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !editing && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, onClose]);

  async function rename(id: number, title: string) {
    const clean = title.trim();
    setEditing(null);
    if (!clean) return;
    try {
      await api.renameInvestigation(id, clean);
      setItems((xs) => xs?.map((x) => (x.id === id ? { ...x, title: clean } : x)) ?? null);
      onRenamed(id, clean);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(item: InvestigationSummary) {
    if (!confirm(`Delete the case file “${item.title}”? Its questions and answers are removed.`)) return;
    const res = await api.deleteInvestigation(item.id);
    if (!res.ok) {
      setError("Could not delete the case file.");
      return;
    }
    setItems((xs) => xs?.filter((x) => x.id !== item.id) ?? null);
    onDeleted(item.id);
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: EASE }}
      role="dialog"
      aria-label="Case files"
      className="absolute inset-0 z-20 flex flex-col bg-ink-900"
    >
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-ink-700 px-3">
        <FolderOpen className="size-3.5 text-lamp" />
        <span className="flex-1 text-xs text-parchment">
          Case files{items ? <span className="text-faint"> · {items.length}</span> : null}
        </span>
        <button onClick={onClose} className="rounded p-1 text-faint hover:bg-ink-800 hover:text-parchment" title="Close">
          <X className="size-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {error ? <p className="p-3 text-xs text-danger">{error}</p> : null}
        {items === null && !error ? (
          <p className="flex items-center gap-2 p-3 text-xs text-muted">
            <Loader2 className="size-3.5 animate-spin" /> Loading…
          </p>
        ) : items?.length === 0 ? (
          <div className="p-6 text-center text-xs text-muted">
            <p className="text-parchment">No case files yet.</p>
            <p className="mt-1">Every question you ask is saved here with its evidence.</p>
          </div>
        ) : (
          <ul className="space-y-1">
            {items?.map((item) => (
              <li key={item.id}>
                {editing?.id === item.id ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      rename(item.id, editing.title);
                    }}
                    className="flex items-center gap-1 rounded-lg border border-lamp/50 bg-ink-950 p-1.5"
                  >
                    <input
                      autoFocus
                      value={editing.title}
                      maxLength={200}
                      aria-label="Case file title"
                      onChange={(e) => setEditing({ id: item.id, title: e.target.value })}
                      onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                      className="min-w-0 flex-1 bg-transparent px-1 text-xs text-parchment outline-none"
                    />
                    <button type="submit" className="rounded p-1 text-evidence hover:bg-ink-800" title="Save">
                      <Check className="size-3.5" />
                    </button>
                  </form>
                ) : (
                  <div
                    className={`group flex items-start gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
                      item.id === currentId
                        ? "border-lamp/40 bg-lamp-soft"
                        : "border-transparent hover:border-ink-700 hover:bg-ink-850"
                    }`}
                  >
                    <button onClick={() => onOpen(item.id)} className="min-w-0 flex-1 text-left">
                      <span className="line-clamp-2 text-[12.5px] leading-snug text-parchment">{item.title}</span>
                      <span className="mt-0.5 block font-mono text-[10px] text-faint">
                        {item.turns} {item.turns === 1 ? "question" : "questions"} · {ago(item.updated_at)}
                        {item.id === currentId ? " · open" : ""}
                      </span>
                    </button>
                    <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                      <button
                        onClick={() => setEditing({ id: item.id, title: item.title })}
                        className="rounded p-1 text-faint hover:bg-ink-800 hover:text-parchment"
                        title="Rename"
                        aria-label={`Rename ${item.title}`}
                      >
                        <Pencil className="size-3" />
                      </button>
                      <button
                        onClick={() => remove(item)}
                        className="rounded p-1 text-faint hover:bg-danger/10 hover:text-danger"
                        title="Delete"
                        aria-label={`Delete ${item.title}`}
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </motion.div>
  );
}
