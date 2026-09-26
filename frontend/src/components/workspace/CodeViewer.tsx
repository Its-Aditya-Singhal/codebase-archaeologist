"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { bundledLanguages, codeToTokens, type ThemedToken } from "shiki";
import { Crosshair } from "lucide-react";
import type { FileDetail, Focus, SymbolInfo } from "@/lib/api";

export interface Highlight {
  start: number;
  end: number;
  tone: "focus" | "evidence";
}

const LANG_ALIASES: Record<string, string> = { bash: "shellscript" };

export function shikiLang(language: string | null): string {
  const l = language ? (LANG_ALIASES[language] ?? language) : "text";
  return l in bundledLanguages ? l : "text";
}

export function CodeViewer({
  file,
  highlight,
  focus,
  onFocus,
}: {
  file: FileDetail | null;
  highlight: Highlight | null;
  focus: Focus | null;
  onFocus: (focus: Focus) => void;
}) {
  const [tokens, setTokens] = useState<{ path: string; lines: ThemedToken[][] } | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const plainLines = useMemo(() => file?.content.split("\n") ?? [], [file]);

  useEffect(() => {
    if (!file) return;
    let cancelled = false;
    codeToTokens(file.content, { lang: shikiLang(file.language) as never, theme: "vitesse-dark" })
      .then((r) => !cancelled && setTokens({ path: file.path, lines: r.tokens }))
      .catch(() => !cancelled && setTokens(null));
    return () => {
      cancelled = true;
    };
  }, [file]);

  // Bring the highlighted evidence (or a newly focused symbol) into view.
  const focusStart = focus?.path === file?.path ? focus?.start_line : undefined;
  useEffect(() => {
    const line = highlight?.start ?? focusStart;
    if (!line || !scroller.current) return;
    const box = scroller.current.getBoundingClientRect();
    const el = scroller.current.querySelector<HTMLElement>(`[data-line="${line}"]`);
    const r = el?.getBoundingClientRect();
    // Only scroll when the target is off-screen, so clicking a visible line doesn't jump.
    if (el && r && (r.top < box.top || r.bottom > box.bottom - 40)) {
      scroller.current.scrollTo({
        top: scroller.current.scrollTop + r.top - box.top - 48,
        behavior: "smooth",
      });
    }
  }, [highlight, focusStart, file]);

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-faint">
        Select a file, or open a cited source from an answer.
      </div>
    );
  }

  const lines = tokens?.path === file.path ? tokens.lines : null;
  const focusHere = focus?.path === file.path ? focus : null;
  const inRange = (n: number, s?: number, e?: number) =>
    s !== undefined && n >= s && n <= (e ?? s);

  function clickLine(n: number, shift: boolean) {
    if (shift && anchor !== null) {
      const [s, e] = anchor < n ? [anchor, n] : [n, anchor];
      onFocus({ path: file!.path, start_line: s, end_line: e, label: `lines ${s}–${e}` });
    } else {
      setAnchor(n);
      onFocus({ path: file!.path, start_line: n, end_line: n, label: `line ${n}` });
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-ink-700 px-4 py-2">
        <span className="truncate font-mono text-xs text-parchment">{file.path}</span>
        <span className="font-mono text-[10px] text-faint">
          {file.language ?? "text"} · {file.line_count} lines
        </span>
        <button
          onClick={() => onFocus({ path: file.path, label: file.path.split("/").pop() })}
          className="ml-auto flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted hover:bg-ink-800 hover:text-lamp"
          title="Investigate this whole file"
        >
          <Crosshair className="size-3" /> File
        </button>
      </div>
      {file.symbols.length ? (
        <SymbolBar symbols={file.symbols} focus={focusHere} onPick={(s) =>
          onFocus({ path: file.path, start_line: s.start_line, end_line: s.end_line, label: s.name })
        } />
      ) : null}
      <div ref={scroller} className="flex-1 overflow-auto bg-ink-950 py-2 font-mono text-[12.5px] leading-[1.6]">
        <table className="w-full border-collapse">
          <tbody>
            {plainLines.map((text, i) => {
              const n = i + 1;
              const isFocus = inRange(n, focusHere?.start_line, focusHere?.end_line);
              const isEvidence = highlight?.tone === "evidence" && inRange(n, highlight.start, highlight.end);
              const bg = isFocus ? "bg-lamp-soft" : isEvidence ? "bg-evidence-soft" : "";
              const bar = isFocus ? "border-lamp" : isEvidence ? "border-evidence" : "border-transparent";
              return (
                <tr key={n} data-line={n} className={bg}>
                  <td
                    onClick={(e) => clickLine(n, e.shiftKey)}
                    className={`w-12 cursor-pointer select-none border-l-2 pr-3 text-right align-top text-faint hover:text-lamp ${bar}`}
                  >
                    {n}
                  </td>
                  <td className="whitespace-pre pr-6 align-top">
                    {lines?.[i]
                      ? lines[i].map((t, j) => (
                          <span key={j} style={{ color: t.color }}>
                            {t.content}
                          </span>
                        ))
                      : text || " "}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="border-t border-ink-700 px-4 py-1 text-[10px] text-faint">
        Click a line number to select it, shift-click to select a range, or pick a symbol above.
      </div>
    </div>
  );
}

function SymbolBar({
  symbols,
  focus,
  onPick,
}: {
  symbols: SymbolInfo[];
  focus: Focus | null;
  onPick: (s: SymbolInfo) => void;
}) {
  return (
    <div className="flex gap-1.5 overflow-x-auto border-b border-ink-700 px-3 py-2">
      {symbols.map((s) => {
        const active = focus?.start_line === s.start_line && focus?.end_line === s.end_line;
        return (
          <button
            key={s.id}
            onClick={() => onPick(s)}
            title={`${s.kind} ${s.name} · lines ${s.start_line}–${s.end_line}${
              s.callers || s.callees ? ` · ${s.callers ?? 0} callers, calls ${s.callees ?? 0}` : ""
            }`}
            className={`shrink-0 rounded border px-2 py-0.5 font-mono text-[11px] transition ${
              active
                ? "border-lamp/60 bg-lamp-soft text-lamp"
                : "border-ink-700 text-muted hover:border-ink-600 hover:text-parchment"
            }`}
          >
            <span className="text-faint">{KIND_GLYPH[s.kind] ?? "·"}</span> {s.name}
            {s.callers ? (
              <span className="ml-1.5 text-[9.5px] text-lamp/70" title={`${s.callers} callers in the dependency graph`}>
                ←{s.callers}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

const KIND_GLYPH: Record<string, string> = {
  function: "ƒ",
  method: "m",
  class: "C",
  interface: "I",
  type: "T",
  struct: "S",
  enum: "E",
  trait: "T",
  impl: "impl",
  section: "§",
};
