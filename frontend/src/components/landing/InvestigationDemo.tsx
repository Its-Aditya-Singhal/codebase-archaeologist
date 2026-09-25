"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useInView, useReducedMotion } from "motion/react";
import {
  CircleDot,
  Code2,
  GitCommitHorizontal,
  GitPullRequest,
  Search,
  Sparkles,
} from "lucide-react";
import { EASE } from "@/components/motion/primitives";

// A fictional repository: the demo illustrates the product, it is not real output.
const QUESTION = "Why does the worker retry failed jobs?";

const SOURCES = [
  {
    ref: "S1",
    icon: Code2,
    kind: "code",
    label: "worker.py:142-151",
    detail: "Worker.handle_failure",
  },
  {
    ref: "S2",
    icon: GitCommitHorizontal,
    kind: "commit",
    label: "4f2a9c1",
    detail: "Retry failed jobs with backoff",
  },
  {
    ref: "S3",
    icon: GitPullRequest,
    kind: "pull request",
    label: "#214",
    detail: "Add backoff to worker retries",
  },
  {
    ref: "S4",
    icon: CircleDot,
    kind: "issue",
    label: "#198",
    detail: "Jobs lost when Redis restarts",
  },
];

const ANSWER =
  "Failed jobs are re-enqueued by `handle_failure` with exponential backoff [S1]. " +
  "The retry cap of 3 arrived in commit `4f2a9c1` [S2], merged in PR #214 [S3], " +
  "to fix issue #198: jobs were silently lost when Redis restarted mid-run [S4].";

const CODE = [
  "class Worker:",
  "    ...",
  "    def handle_failure(self, job, exc):",
  "        if job.retries < self.max_retries:",
  "            delay = 2 ** job.retries",
  "            job.retries += 1",
  "            self.queue.enqueue_in(delay, job)",
  "            return",
  "        self.failed_registry.add(job, exc)",
  '        log.warning("job %s failed", job.id)',
];
const HIGHLIGHT = [2, 3, 4, 5, 6];

type Phase = "typing" | "searching" | "sources" | "answering" | "done";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function InvestigationDemo() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: "-15% 0px" });
  const reduce = useReducedMotion();
  const [typedState, setTyped] = useState(0);
  const [phaseState, setPhase] = useState<Phase>("typing");
  const [sourcesState, setSources] = useState(0);
  const [answerState, setAnswer] = useState(0);
  // Reduced motion: show the finished investigation, no playback.
  const typed = reduce ? QUESTION.length : typedState;
  const phase: Phase = reduce ? "done" : phaseState;
  const sources = reduce ? SOURCES.length : sourcesState;
  const answer = reduce ? ANSWER.length : answerState;

  useEffect(() => {
    if (reduce || !inView) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        setPhase("typing");
        setTyped(0);
        setSources(0);
        setAnswer(0);
        await wait(500);
        for (let i = 1; i <= QUESTION.length && !cancelled; i++) {
          setTyped(i);
          await wait(38);
        }
        setPhase("searching");
        await wait(1100);
        setPhase("sources");
        for (let i = 1; i <= SOURCES.length && !cancelled; i++) {
          setSources(i);
          await wait(380);
        }
        setPhase("answering");
        for (let i = 0; i <= ANSWER.length && !cancelled; i += 3) {
          setAnswer(i);
          await wait(22);
        }
        setAnswer(ANSWER.length);
        setPhase("done");
        await wait(5200);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [inView, reduce]);

  const codeLit = sources >= 1;

  return (
    <div
      ref={ref}
      className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900/90 shadow-[0_30px_120px_-30px_rgba(0,0,0,0.9)] backdrop-blur"
      role="img"
      aria-label={`Demo: asking "${QUESTION}" returns four cited sources (code, commit, pull request, issue) and a cited answer.`}
    >
      <div className="flex h-10 items-center gap-2 border-b border-ink-700 bg-ink-850 px-4">
        <span className="size-2.5 rounded-full bg-ink-600" />
        <span className="size-2.5 rounded-full bg-ink-600" />
        <span className="size-2.5 rounded-full bg-ink-600" />
        <span className="ml-3 truncate font-mono text-[11px] text-faint">
          acme/jobqueue <span className="text-ink-600">/</span> worker.py
        </span>
        <span className="ml-auto hidden font-mono text-[10px] uppercase tracking-widest text-evidence sm:block">
          ready · 1,284 commits · 312 PRs
        </span>
      </div>

      <div className="grid md:grid-cols-[1.05fr_1fr]" aria-hidden>
        {/* Code pane */}
        <div className="hidden border-r border-ink-700 py-4 font-mono text-[12.5px] leading-6 md:block">
          {CODE.map((line, i) => {
            const lit = codeLit && HIGHLIGHT.includes(i);
            return (
              <div
                key={i}
                className={`flex pr-4 transition-colors duration-500 ${lit ? "bg-lamp-soft" : ""}`}
              >
                <span
                  className={`w-12 shrink-0 select-none pr-4 text-right transition-colors duration-500 ${lit ? "text-lamp" : "text-ink-600"}`}
                >
                  {140 + i}
                </span>
                <span className="whitespace-pre text-parchment/85">{line}</span>
              </div>
            );
          })}
        </div>

        {/* Investigation pane */}
        <div className="flex min-h-[380px] flex-col gap-4 p-4 sm:p-5">
          <div className="flex items-center gap-2 rounded-lg border border-ink-700 bg-ink-950 px-3 py-2.5">
            <Search className="size-4 shrink-0 text-faint" />
            <span className="text-sm text-parchment">
              {QUESTION.slice(0, typed)}
              {phase === "typing" ? <span className="streaming-caret" /> : null}
            </span>
          </div>

          <AnimatePresence mode="wait">
            {phase === "searching" ? (
              <motion.div
                key="searching"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-wrap gap-2"
              >
                {["vector", "keyword", "symbol", "graph", "history"].map((s, i) => (
                  <motion.span
                    key={s}
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: [0.4, 1, 0.4], scale: 1 }}
                    transition={{
                      duration: 1,
                      repeat: Infinity,
                      delay: i * 0.12,
                    }}
                    className="rounded-full border border-ink-700 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted"
                  >
                    {s}
                  </motion.span>
                ))}
              </motion.div>
            ) : null}
          </AnimatePresence>

          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {SOURCES.slice(0, sources).map((s) => (
              <motion.li
                key={s.ref}
                initial={{ opacity: 0, y: 10, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.45, ease: EASE }}
                className="flex items-start gap-2 rounded-lg border border-ink-700 bg-ink-850 p-2.5"
              >
                <span className="rounded bg-evidence-soft px-1.5 py-px font-mono text-[10px] font-semibold text-evidence">
                  {s.ref}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 font-mono text-[11px] text-parchment">
                    <s.icon className="size-3 text-faint" />
                    {s.label}
                  </div>
                  <div className="truncate text-[11px] text-muted">{s.detail}</div>
                </div>
              </motion.li>
            ))}
          </ul>

          {answer > 0 ? (
            <div className="rounded-lg border border-ink-700 bg-ink-950/70 p-3.5">
              <div className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-lamp">
                <Sparkles className="size-3" /> Answer
              </div>
              <p className="text-[13px] leading-relaxed text-parchment/90">
                <Rich text={ANSWER.slice(0, answer)} />
                {phase === "answering" ? <span className="streaming-caret" /> : null}
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Renders `code` and [S#] citations inside the streamed answer. */
function Rich({ text }: { text: string }) {
  const parts = text.split(/(\[S\d\]|`[^`]*`)/g);
  return (
    <>
      {parts.map((p, i) =>
        /^\[S\d\]$/.test(p) ? (
          <span
            key={i}
            className="mx-0.5 rounded bg-evidence-soft px-1 font-mono text-[10px] font-semibold text-evidence"
          >
            {p.slice(1, -1)}
          </span>
        ) : /^`[^`]*`$/.test(p) ? (
          <code key={i} className="rounded bg-ink-800 px-1 font-mono text-[11.5px] text-lamp">
            {p.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
