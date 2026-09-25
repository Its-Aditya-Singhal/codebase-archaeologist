import Link from "next/link";
import {
  ArrowRight,
  Bot,
  FolderSearch,
  GitBranch,
  History,
  Layers3,
  Network,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { CountUp, GlowCard, Reveal, Stagger, StaggerItem } from "@/components/motion/primitives";
import { LogoMark } from "@/components/Logo";
import { CtaButtons } from "./CtaButtons";

// ------------------------------------------------------------------ marquee

const QUESTIONS_A = [
  "Why does this function retry three times?",
  "Which pull request introduced the cache?",
  "What breaks if I change parse_config?",
  "Who calls enqueue()?",
  "How did this class evolve?",
  "Which issue led to this workaround?",
];
const QUESTIONS_B = [
  "Where is authentication handled?",
  "Why is this dependency pinned?",
  "What tests cover the scheduler?",
  "When did the API switch to async?",
  "Who wrote the retry logic, and why?",
  "What else changed in that PR?",
];

function Row({ items, reverse = false }: { items: string[]; reverse?: boolean }) {
  return (
    <div className="flex overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_12%,#000_88%,transparent)]">
      <div className={`marquee flex shrink-0 gap-3 pr-3 ${reverse ? "marquee-reverse" : ""}`}>
        {[...items, ...items].map((q, i) => (
          <span
            key={i}
            aria-hidden={i >= items.length}
            className="shrink-0 rounded-full border border-ink-700 bg-ink-900/70 px-4 py-2 text-sm whitespace-nowrap text-muted transition-colors duration-200 hover:border-lamp/50 hover:text-parchment"
          >
            <span className="mr-2 font-mono text-lamp">?</span>
            {q}
          </span>
        ))}
      </div>
    </div>
  );
}

export function QuestionMarquee() {
  return (
    <section aria-label="Questions you can ask" className="marquee-wrap space-y-3 py-16">
      <Reveal>
        <p className="mb-8 text-center font-mono text-xs uppercase tracking-[0.25em] text-faint">
          Questions it answers with receipts
        </p>
      </Reveal>
      <Row items={QUESTIONS_A} />
      <Row items={QUESTIONS_B} reverse />
    </section>
  );
}

// ----------------------------------------------------------------- features

function Card({
  icon: Icon,
  title,
  body,
  className = "",
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <StaggerItem className={className}>
      <GlowCard
        as="article"
        className="group h-full rounded-2xl border border-ink-700 bg-ink-900/70 p-6 transition-transform duration-300 ease-out hover:-translate-y-1"
      >
        <div className="relative z-10 flex h-full flex-col">
          <span className="flex size-11 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-lamp transition-all duration-300 group-hover:-rotate-6 group-hover:border-lamp/50 group-hover:bg-lamp-soft">
            <Icon className="size-5" />
          </span>
          <h3 className="mt-5 text-lg font-medium text-parchment">{title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
          {children ? <div className="mt-auto pt-6">{children}</div> : null}
        </div>
      </GlowCard>
    </StaggerItem>
  );
}

const SIGNALS = [
  { name: "vector", w: "w-[78%]" },
  { name: "keyword", w: "w-[54%]" },
  { name: "symbol", w: "w-[91%]" },
  { name: "graph", w: "w-[66%]" },
];

export function Features() {
  return (
    <section id="features" className="py-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Reveal className="max-w-2xl">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">The toolkit</p>
          <h2 className="mt-4 font-display text-4xl leading-[1.05] text-parchment sm:text-5xl">
            An investigator, not a chatbot.
          </h2>
          <p className="mt-5 leading-relaxed text-muted">
            Every tool works from evidence it can show you. When the sources don&apos;t answer a
            question, it says so instead of guessing.
          </p>
        </Reveal>

        <Stagger className="mt-14 grid gap-4 md:grid-cols-3">
          <Card
            className="md:col-span-2"
            icon={FolderSearch}
            title="Hybrid retrieval"
            body="Four signals ranked together: semantic meaning, exact keywords, symbol names and graph relationships, fused with reciprocal rank fusion and anchored on the code you have selected."
          >
            <div className="space-y-2">
              {SIGNALS.map((s, i) => (
                <div key={s.name} className="flex items-center gap-3">
                  <span className="w-16 font-mono text-[10px] uppercase tracking-wider text-faint">
                    {s.name}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
                    <div
                      className={`h-full origin-left scale-x-[0.35] rounded-full bg-lamp/70 transition-transform duration-700 ease-out group-hover:scale-x-100 ${s.w}`}
                      style={{ transitionDelay: `${i * 70}ms` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card
            icon={Network}
            title="Knowledge graph"
            body="Callers, callees, base classes, imports and packages, resolved across the whole repository."
          >
            <svg viewBox="0 0 200 70" className="h-16 w-full" aria-hidden>
              {[
                [30, 35, 100, 15],
                [30, 35, 100, 55],
                [100, 15, 170, 35],
                [100, 55, 170, 35],
              ].map(([x1, y1, x2, y2], i) => (
                <line
                  key={i}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="var(--ink-600)"
                  className="transition-[stroke] duration-500 group-hover:stroke-[var(--lamp)]"
                  style={{ transitionDelay: `${i * 80}ms` }}
                />
              ))}
              {[
                [30, 35],
                [100, 15],
                [100, 55],
                [170, 35],
              ].map(([cx, cy], i) => (
                <circle
                  key={i}
                  cx={cx}
                  cy={cy}
                  r="6"
                  fill="var(--ink-850)"
                  stroke="var(--lamp)"
                  className="origin-center transition-transform duration-300 [transform-box:fill-box] group-hover:scale-125"
                />
              ))}
            </svg>
          </Card>

          <Card
            icon={ShieldAlert}
            title="Impact analysis"
            body="Before you change something: every dependent three hops out, the tests that reach it, files that change alongside it, and a risk level with its reasons."
          />
          <Card
            icon={History}
            title="Evolution"
            body="Step through a function's versions over time, each with its diff and the pull request that changed it."
          >
            <div className="flex items-center gap-1.5" aria-hidden>
              {[0, 1, 2, 3, 4].map((i) => (
                <span
                  key={i}
                  className="h-1.5 flex-1 rounded-full bg-ink-700 transition-colors duration-300 group-hover:bg-lamp"
                  style={{ transitionDelay: `${i * 90}ms` }}
                />
              ))}
            </div>
          </Card>
          <Card
            icon={Bot}
            title="Agent mode"
            body="For multi-hop questions, a model uses seven tools to search, read code, trace history and follow the graph, and you see each step it takes."
          />

          <Card
            icon={Wallet}
            title="Free by default"
            body="Local embeddings, a free Gemini key or a local model, or no model at all: a cited evidence briefing."
          />
          <Card
            className="md:col-span-2"
            icon={Layers3}
            title="Case files"
            body="Investigations are saved with the exact evidence behind each answer. Ask follow-ups like “and who calls it?”, and the context carries over, so you can reopen the thread weeks later."
          >
            <div className="flex flex-wrap gap-2" aria-hidden>
              {[
                "Why does the worker retry?",
                "and who calls it?",
                "what breaks if I remove it?",
              ].map((q, i) => (
                <span
                  key={q}
                  className="rounded-lg border border-ink-700 bg-ink-850 px-2.5 py-1 font-mono text-[11px] text-muted transition-all duration-300 group-hover:border-lamp/40 group-hover:text-parchment"
                  style={{ transitionDelay: `${i * 80}ms` }}
                >
                  {i ? "↳ " : ""}
                  {q}
                </span>
              ))}
            </div>
          </Card>
        </Stagger>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ numbers

const NUMBERS = [
  { value: 6, label: "links in the evidence chain", suffix: "" },
  { value: 4, label: "retrieval signals, fused", suffix: "" },
  { value: 7, label: "tools in agent mode", suffix: "" },
  { value: 0, label: "paid services required", suffix: "" },
];

export function Numbers() {
  return (
    <section aria-label="By the numbers" className="border-y border-ink-700 bg-ink-900/40">
      <Stagger className="mx-auto grid max-w-6xl grid-cols-2 divide-ink-700 px-4 sm:px-6 md:grid-cols-4 md:divide-x">
        {NUMBERS.map((n) => (
          <StaggerItem key={n.label} className="px-4 py-10 text-center md:py-14">
            <div className="font-display text-5xl text-parchment sm:text-6xl">
              <CountUp value={n.value} suffix={n.suffix} />
            </div>
            <div className="mt-2 text-sm text-muted">{n.label}</div>
          </StaggerItem>
        ))}
      </Stagger>
    </section>
  );
}

// -------------------------------------------------------------------- steps

const STEPS = [
  {
    title: "Point it at a repository",
    body: "A GitHub URL, owner/repo, or a local checkout. Private repositories work with a token.",
    cmd: "github.com/acme/jobqueue",
  },
  {
    title: "It excavates",
    body: "Parses the code, embeds it locally, reads the git history, syncs pull requests and issues, and builds the knowledge graph. Re-indexing only touches what changed.",
    cmd: "parsing → embedding → history → graph",
  },
  {
    title: "Ask, then verify",
    body: "Select code or just ask. Answers stream in with [S#] citations. Click one to open the exact lines, commit, PR or issue it came from.",
    cmd: "“why does this exist?” → [S1][S3]",
  },
];

export function Steps() {
  return (
    <section id="how" className="py-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Reveal className="max-w-2xl">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">How it works</p>
          <h2 className="mt-4 font-display text-4xl leading-[1.05] text-parchment sm:text-5xl">
            Three steps from unfamiliar to understood.
          </h2>
        </Reveal>
        <Stagger className="relative mt-16 grid gap-10 md:grid-cols-3 md:gap-6">
          <div
            aria-hidden
            className="absolute top-7 right-[16%] left-[16%] hidden h-px bg-gradient-to-r from-lamp/0 via-lamp/40 to-lamp/0 md:block"
          />
          {STEPS.map((s, i) => (
            <StaggerItem key={s.title} className="relative">
              <div className="group">
                <span className="relative z-10 flex size-14 items-center justify-center rounded-full border border-ink-700 bg-ink-950 font-display text-2xl text-lamp transition-all duration-300 group-hover:border-lamp/60 group-hover:shadow-[0_0_30px_-4px_rgba(233,162,59,0.5)]">
                  {i + 1}
                </span>
                <h3 className="mt-6 text-lg font-medium text-parchment">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">{s.body}</p>
                <div className="mt-4 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-[11.5px] text-faint transition-colors duration-300 group-hover:text-parchment/80">
                  <span className="text-lamp">$ </span>
                  {s.cmd}
                </div>
              </div>
            </StaggerItem>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- cta + foot

export function FinalCta() {
  return (
    <section className="grain relative overflow-hidden py-36">
      <div aria-hidden className="survey-grid grid-fade absolute inset-0" />
      <div
        aria-hidden
        className="breathe absolute top-1/2 left-1/2 size-[560px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-lamp/15 blur-[120px]"
      />
      <Reveal className="relative mx-auto max-w-3xl px-4 text-center sm:px-6">
        <GitBranch className="mx-auto size-6 text-lamp" />
        <h2 className="mt-6 font-display text-5xl leading-[1.02] text-parchment sm:text-7xl">
          Every answer comes with its <em className="text-lamp">receipts</em>.
        </h2>
        <p className="mx-auto mt-6 max-w-xl text-muted">
          Stop reverse-engineering intent from code alone. Dig up the commits, reviews and bug
          reports that explain it.
        </p>
        <CtaButtons />
      </Reveal>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-ink-700">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 py-10 text-sm text-faint sm:flex-row sm:px-6">
        <div className="flex items-center gap-3">
          <LogoMark className="size-6" />
          <span>
            Codebase Archaeologist. For developers inheriting code they didn&apos;t write.
          </span>
        </div>
        <nav aria-label="Footer" className="flex gap-5">
          <a href="#features" className="transition-colors hover:text-parchment">
            Features
          </a>
          <a href="#how" className="transition-colors hover:text-parchment">
            How it works
          </a>
          <Link href="/login" className="transition-colors hover:text-parchment">
            Sign in
          </Link>
          <Link href="/signup" className="group flex items-center gap-1 text-lamp">
            Get started
            <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </nav>
      </div>
    </footer>
  );
}
