"use client";

import { useMemo, useState } from "react";
import { ChevronRight, FileCode2, FileText, Search } from "lucide-react";
import type { RepoFile } from "@/lib/api";

interface DirNode {
  name: string;
  path: string;
  dirs: Map<string, DirNode>;
  files: RepoFile[];
}

function buildTree(files: RepoFile[]): DirNode {
  const root: DirNode = { name: "", path: "", dirs: new Map(), files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      const path = node.path ? `${node.path}/${part}` : part;
      if (!node.dirs.has(part)) node.dirs.set(part, { name: part, path, dirs: new Map(), files: [] });
      node = node.dirs.get(part)!;
    }
    node.files.push(f);
  }
  return root;
}

export function FileExplorer({
  files,
  selected,
  onSelect,
}: {
  files: RepoFile[];
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set());

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files;
  }, [files, filter]);
  const tree = useMemo(() => buildTree(filtered), [filtered]);

  // Selected file's ancestors are always expanded; so is everything while filtering.
  const isOpen = (path: string) =>
    filter.trim() !== "" || open.has(path) || (selected?.startsWith(path + "/") ?? false);
  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (isOpen(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <Search className="size-3.5 text-faint" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter files"
          className="w-full bg-transparent text-xs outline-none placeholder:text-faint"
        />
      </div>
      <div className="flex-1 overflow-y-auto py-1 text-[13px]">
        <Dir node={tree} depth={0} isOpen={isOpen} toggle={toggle} selected={selected} onSelect={onSelect} />
      </div>
      <div className="border-t border-ink-700 px-3 py-1.5 font-mono text-[10px] text-faint">
        {filtered.length} files
      </div>
    </div>
  );
}

function Dir({
  node,
  depth,
  isOpen,
  toggle,
  selected,
  onSelect,
}: {
  node: DirNode;
  depth: number;
  isOpen: (p: string) => boolean;
  toggle: (p: string) => void;
  selected: string | null;
  onSelect: (p: string) => void;
}) {
  const dirs = [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name));
  const pad = (d: number) => ({ paddingLeft: 8 + d * 12 });
  return (
    <>
      {dirs.map((d) => (
        <div key={d.path}>
          <button
            onClick={() => toggle(d.path)}
            style={pad(depth)}
            className="flex w-full items-center gap-1 py-[3px] pr-2 text-left text-muted hover:bg-ink-800 hover:text-parchment"
          >
            <ChevronRight
              className={`size-3 shrink-0 transition-transform ${isOpen(d.path) ? "rotate-90" : ""}`}
            />
            <span className="truncate">{d.name}</span>
          </button>
          {isOpen(d.path) ? (
            <Dir node={d} depth={depth + 1} isOpen={isOpen} toggle={toggle} selected={selected} onSelect={onSelect} />
          ) : null}
        </div>
      ))}
      {node.files.map((f) => {
        const name = f.path.split("/").pop();
        const active = f.path === selected;
        const Icon = f.language === "markdown" ? FileText : FileCode2;
        return (
          <button
            key={f.path}
            onClick={() => onSelect(f.path)}
            style={pad(depth)}
            title={f.path}
            className={`flex w-full items-center gap-1.5 py-[3px] pr-2 text-left ${
              active
                ? "bg-lamp-soft text-parchment"
                : "text-muted hover:bg-ink-800 hover:text-parchment"
            }`}
          >
            <Icon className={`ml-4 size-3.5 shrink-0 ${active ? "text-lamp" : "text-faint"}`} />
            <span className="truncate">{name}</span>
            {f.symbols > 0 ? (
              <span className="ml-auto font-mono text-[10px] text-faint">{f.symbols}</span>
            ) : null}
          </button>
        );
      })}
    </>
  );
}
