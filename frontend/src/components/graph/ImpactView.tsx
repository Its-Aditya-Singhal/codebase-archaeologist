"use client";

import { useMemo, useState } from "react";
import { FlaskConical, GitCompareArrows, Radar, ShieldAlert } from "lucide-react";
import type { Dependent, Impact } from "@/lib/api";
import { formatDate } from "@/components/history/parts";
import { NodeGlyph } from "./parts";

const RISK_TONE = {
  low: "text-evidence border-evidence/40 bg-evidence-soft",
  medium: "text-lamp border-lamp/40 bg-lamp-soft",
  high: "text-danger border-danger/40 bg-danger/10",
} as const;

export function ImpactView({
  data,
  onOpen,
}: {
  data: Impact;
  onOpen: (d: { path: string | null; start_line: number | null; end_line: number | null; label: string }) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const { summary: s, risk } = data;
  const direct = data.dependents.filter((d) => d.depth === 1 && !d.is_test);
  const indirect = data.dependents.filter((d) => d.depth > 1 && !d.is_test);
  const testFiles = data.files.filter((f) => f.is_test);

  return (
    <div className="mx-auto max-w-5xl px-6 py-5">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div>
          <div className="flex items-start gap-3">
            <div className={`rounded-lg border px-3 py-2 ${RISK_TONE[risk.level]}`}>
              <div className="font-mono text-[9.5px] uppercase tracking-[0.2em] opacity-80">change risk</div>
              <div className="font-display text-3xl leading-none">{risk.level}</div>
            </div>
            <div className="min-w-0 pt-0.5">
              <p className="text-[13px] leading-snug text-parchment">
                Changing <span className="font-mono text-lamp">{data.target.label}</span>{" "}
                {s.transitive
                  ? `could affect ${s.transitive} other definition${s.transitive === 1 ? "" : "s"} in ${s.files} file${s.files === 1 ? "" : "s"}.`
                  : "affects no other production code the static graph can see."}
              </p>
              <ul className="mt-1.5 space-y-0.5 text-[11.5px] text-muted">
                {risk.reasons.map((r) => (
                  <li key={r} className="flex gap-1.5">
                    <ShieldAlert className="mt-0.5 size-3 shrink-0 text-faint" />
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="direct dependents" value={s.direct} />
            <Stat label="reach (≤3 hops)" value={s.transitive} />
            <Stat label="files affected" value={s.files} />
            <Stat label="tests reaching it" value={s.tests} tone={s.tests ? "evidence" : "danger"} />
          </div>

          <Section title="Direct dependents" icon={Radar} count={direct.length}>
            {direct.length ? (
              direct.map((d) => (
                <DependentRow key={d.id} d={d} hot={hover === d.id} onHover={setHover} onOpen={onOpen} />
              ))
            ) : (
              <Empty>No production code calls, imports or subclasses this directly.</Empty>
            )}
          </Section>

          {indirect.length ? (
            <Section title="Indirect dependents" icon={Radar} count={indirect.length}>
              {indirect.slice(0, 40).map((d) => (
                <DependentRow key={d.id} d={d} hot={hover === d.id} onHover={setHover} onOpen={onOpen} />
              ))}
            </Section>
          ) : null}

          <Section title="Tests that reach it" icon={FlaskConical} count={s.tests}>
            {testFiles.length ? (
              <div className="flex flex-wrap gap-1.5">
                {testFiles.map((f) => (
                  <button
                    key={f.path}
                    onClick={() => onOpen({ path: f.path, start_line: null, end_line: null, label: f.path })}
                    className="rounded border border-ink-700 px-2 py-0.5 font-mono text-[10.5px] text-muted hover:border-evidence/50 hover:text-parchment"
                  >
                    {f.path} <span className="text-evidence">{f.symbols}</span>
                    {f.depth > 1 ? <span className="text-faint"> · {f.depth} hops</span> : null}
                  </button>
                ))}
              </div>
            ) : (
              <Empty>
                No test in the static call graph reaches this code. Tests that exercise it through
                dynamic dispatch or fixtures would not show up here.
              </Empty>
            )}
          </Section>
        </div>

        <div className="space-y-5">
          <BlastRadius data={data} hover={hover} onHover={setHover} onOpen={onOpen} />
          <div>
            <h3 className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-faint">
              <GitCompareArrows className="size-3.5" /> Changes together with
            </h3>
            {data.co_changed.length ? (
              <div className="mt-2 space-y-1.5">
                {data.co_changed.map((c) => (
                  <button
                    key={c.path}
                    onClick={() => onOpen({ path: c.path, start_line: null, end_line: null, label: c.path })}
                    className="block w-full text-left"
                    title={`${c.together} of ${data.churn.file_commits} commits that touched ${data.target.path}`}
                  >
                    <div className="flex justify-between gap-2 font-mono text-[10.5px]">
                      <span className="truncate text-muted hover:text-parchment">{c.path}</span>
                      <span className="shrink-0 text-faint">{Math.round(c.ratio * 100)}%</span>
                    </div>
                    <div className="mt-0.5 h-1 rounded bg-ink-800">
                      <div
                        className={`h-1 rounded ${c.is_test ? "bg-evidence/60" : "bg-lamp/70"}`}
                        style={{ width: `${Math.max(4, c.ratio * 100)}%` }}
                      />
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <Empty>No file changes together with this one in the history.</Empty>
            )}
            <p className="mt-3 text-[11px] leading-relaxed text-faint">
              {data.target.path} changed in {data.churn.commits} commits by {data.churn.authors} authors
              {data.churn.last_changed ? `, most recently ${formatDate(data.churn.last_changed)}` : ""}.
              Co-change comes from history, so it catches coupling static analysis can&apos;t see.
            </p>
          </div>
        </div>
      </div>
      <p className="mt-8 border-t border-ink-700 pt-3 text-[11px] leading-relaxed text-faint">
        Built from name-based static analysis of calls, imports and inheritance. Dashed or low-confidence
        links were inferred by name. Dynamic dispatch, reflection and callbacks passed as values
        are not visible, so treat this as a floor, not a ceiling.
      </p>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "evidence" | "danger" }) {
  return (
    <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2">
      <div
        className={`font-display text-2xl leading-none ${
          tone === "danger" ? "text-danger" : tone === "evidence" ? "text-evidence" : "text-parchment"
        }`}
      >
        {value}
      </div>
      <div className="mt-1 font-mono text-[9.5px] uppercase tracking-[0.14em] text-faint">{label}</div>
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  count,
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6">
      <h3 className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-faint">
        <Icon className="size-3.5" /> {title} <span className="text-muted">{count}</span>
      </h3>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-[11.5px] leading-relaxed text-faint">{children}</p>;
}

function DependentRow({
  d,
  hot,
  onHover,
  onOpen,
}: {
  d: Dependent;
  hot: boolean;
  onHover: (id: number | null) => void;
  onOpen: (d: Dependent) => void;
}) {
  return (
    <button
      onClick={() => onOpen(d)}
      onMouseEnter={() => onHover(d.id)}
      onMouseLeave={() => onHover(null)}
      className={`flex w-full items-center gap-2 rounded border px-2 py-1 text-left transition ${
        hot ? "border-lamp/50 bg-lamp-soft" : "border-ink-700 hover:border-ink-600 hover:bg-ink-850"
      }`}
    >
      <NodeGlyph node={d} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[11.5px] text-parchment">{d.label}</span>
        <span className="block truncate font-mono text-[9.5px] text-faint">
          {d.path}:{d.start_line}
          {d.depth > 1 && d.through ? ` · via ${d.through}` : ""}
        </span>
      </span>
      <span className="shrink-0 font-mono text-[9.5px] text-faint">
        {d.edge === "calls" ? "calls" : d.edge === "inherits" ? "subclass" : "imports"}
        {d.confidence < 0.9 ? <span className="text-lamp/70"> · ~{d.confidence.toFixed(1)}</span> : null}
      </span>
    </button>
  );
}

const RINGS = [0, 52, 94, 136]; // radius per hop distance

/** Dependents on rings by hop distance: the shape of the blast radius at a glance. */
function BlastRadius({
  data,
  hover,
  onHover,
  onOpen,
}: {
  data: Impact;
  hover: number | null;
  onHover: (id: number | null) => void;
  onOpen: (d: Dependent) => void;
}) {
  const size = 300;
  const c = size / 2;
  const dots = useMemo(() => {
    const out: { d: Dependent; x: number; y: number }[] = [];
    for (const depth of [1, 2, 3]) {
      const ds = data.dependents
        .filter((d) => d.depth === depth)
        .sort((a, b) => (a.path ?? "").localeCompare(b.path ?? ""))
        .slice(0, 90);
      ds.forEach((d, i) => {
        const a = (i / ds.length) * Math.PI * 2 - Math.PI / 2 + depth * 0.35;
        out.push({ d, x: c + Math.cos(a) * RINGS[depth], y: c + Math.sin(a) * RINGS[depth] });
      });
    }
    return out;
  }, [data, c]);
  const hot = dots.find((p) => p.d.id === hover);

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/60 p-3">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-faint">Blast radius</h3>
      <svg viewBox={`0 0 ${size} ${size}`} className="mt-1 w-full">
        {RINGS.slice(1).map((r, i) => (
          <g key={r}>
            <circle cx={c} cy={c} r={r} fill="none" stroke="var(--ink-700)" strokeDasharray={i ? "2 5" : undefined} />
            <text x={c + 4} y={c - r - 4} fill="var(--faint)" fontSize="8" fontFamily="var(--font-geist-mono)">
              {i + 1} hop{i ? "s" : ""}
            </text>
          </g>
        ))}
        {hot ? <line x1={c} y1={c} x2={hot.x} y2={hot.y} stroke="var(--lamp)" strokeOpacity="0.5" /> : null}
        <circle cx={c} cy={c} r={9} fill="var(--lamp)" />
        <circle cx={c} cy={c} r={16} fill="none" stroke="var(--lamp)" strokeOpacity="0.35" />
        {dots.map(({ d, x, y }) => (
          <circle
            key={d.id}
            cx={x}
            cy={y}
            r={hover === d.id ? 5.5 : 3.6}
            fill={d.is_test ? "transparent" : d.depth === 1 ? "var(--lamp)" : "var(--parchment)"}
            fillOpacity={d.is_test ? 0 : d.depth === 1 ? 0.95 : 0.55}
            stroke={d.is_test ? "var(--evidence)" : "none"}
            strokeWidth={1.2}
            className="cursor-pointer transition-all"
            onMouseEnter={() => onHover(d.id)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onOpen(d)}
          >
            <title>{`${d.label}\n${d.path}${d.is_test ? " (test)" : ""}`}</title>
          </circle>
        ))}
      </svg>
      <div className="flex min-h-8 items-center justify-center text-center font-mono text-[10px] text-muted">
        {hot ? (
          <span className="truncate">
            {hot.d.label} <span className="text-faint">· {hot.d.path}</span>
          </span>
        ) : (
          <span className="flex gap-3 text-faint">
            <span><span className="text-lamp">●</span> direct</span>
            <span><span className="text-parchment/60">●</span> indirect</span>
            <span><span className="text-evidence">○</span> test</span>
          </span>
        )}
      </div>
    </div>
  );
}
