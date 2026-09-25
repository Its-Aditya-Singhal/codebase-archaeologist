"use client";

import { useRef, useState } from "react";
import {
  AnimatePresence,
  motion,
  useMotionValueEvent,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue,
} from "motion/react";
import { CircleDot, Code2, GitCommitHorizontal, GitPullRequest } from "lucide-react";
import { EASE } from "@/components/motion/primitives";

const LAYERS = [
  {
    era: "HEAD",
    title: "Code",
    question: "What does it do?",
    body: "Tree-sitter parses every file into functions, classes and methods. Hybrid search blends meaning, keywords, symbol names and the dependency graph to find the code a question is about.",
    icon: Code2,
    specimen: "def handle_failure(self, job, exc):",
    tone: "from-[#2b2f37] to-[#23262d]",
  },
  {
    era: "2023",
    title: "Commits",
    question: "When did it change?",
    body: "Line-level history finds the commit that introduced the code and every change since, with the diff of exactly those lines. Even across renames and merges.",
    icon: GitCommitHorizontal,
    specimen: "4f2a9c1  Retry failed jobs with backoff",
    tone: "from-[#2d2a25] to-[#25221e]",
  },
  {
    era: "2021",
    title: "Pull requests",
    question: "Why was it merged?",
    body: "Each commit is traced to the pull request it shipped in, including the description and the review discussion where the trade-offs were argued.",
    icon: GitPullRequest,
    specimen: "#214  Add backoff to worker retries · merged",
    tone: "from-[#2e2720] to-[#261f1a]",
  },
  {
    era: "2019",
    title: "Issues",
    question: "What problem did it solve?",
    body: "Pull requests lead to the issues they fixed or referenced: the original bug report, the user who hit it, the reason the code exists at all.",
    icon: CircleDot,
    specimen: "#198  Jobs lost when Redis restarts",
    tone: "from-[#2f241c] to-[#271d16]",
  },
];

export function Strata() {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end end"],
  });
  const depth = useSpring(scrollYProgress, { stiffness: 100, damping: 24 });
  const [active, setActive] = useState(0);
  useMotionValueEvent(scrollYProgress, "change", (p) =>
    setActive(Math.min(LAYERS.length - 1, Math.max(0, Math.floor(p * LAYERS.length * 0.999)))),
  );
  const layer = LAYERS[active];

  return (
    <section id="layers" ref={ref} className="relative h-[340vh]" aria-label="Layers of evidence">
      <div className="sticky top-0 flex h-screen items-center overflow-hidden">
        <div className="mx-auto grid w-full max-w-6xl grid-cols-1 items-center gap-8 px-4 sm:px-6 lg:grid-cols-[1fr_1.1fr] lg:gap-12">
          <div className="min-w-0">
            <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">Stratigraphy</p>
            <h2 className="mt-4 font-display text-3xl leading-[1.05] text-parchment sm:text-5xl">
              Every line of code sits on layers of history.
            </h2>
            <div className="mt-6 min-h-[170px] sm:mt-8 sm:min-h-[190px]">
              <AnimatePresence mode="wait">
                <motion.div
                  key={layer.title}
                  initial={{ opacity: 0, y: 18, filter: "blur(4px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: -14, filter: "blur(4px)" }}
                  transition={{ duration: 0.45, ease: EASE }}
                >
                  <div className="flex items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-xl border border-lamp/40 bg-lamp-soft text-lamp">
                      <layer.icon className="size-5" />
                    </span>
                    <div>
                      <div className="font-mono text-[11px] uppercase tracking-widest text-faint">
                        Layer {active + 1} of {LAYERS.length} · {layer.title}
                      </div>
                      <div className="text-xl text-parchment">{layer.question}</div>
                    </div>
                  </div>
                  <p className="mt-4 max-w-md text-sm leading-relaxed text-muted sm:text-base">{layer.body}</p>
                </motion.div>
              </AnimatePresence>
            </div>
            <div className="mt-6 flex gap-2" aria-hidden>
              {LAYERS.map((l, i) => (
                <span
                  key={l.title}
                  className={`h-1 rounded-full transition-all duration-500 ${i === active ? "w-10 bg-lamp" : i < active ? "w-4 bg-lamp/40" : "w-4 bg-ink-700"}`}
                />
              ))}
            </div>
          </div>

          <div className="relative min-w-0" aria-hidden>
            <Probe depth={depth} />
            <div className="space-y-2 pl-10 sm:space-y-3 sm:pl-14">
              {LAYERS.map((l, i) => (
                <Slab
                  key={l.title}
                  layer={l}
                  index={i}
                  progress={scrollYProgress}
                  active={i === active}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/** The drill line descending through the layers, with a depth readout. */
function Probe({ depth }: { depth: MotionValue<number> }) {
  const height = useTransform(depth, [0, 1], ["4%", "100%"]);
  const years = useTransform(depth, (d) => `−${Math.round(d * 7)}y`);
  return (
    <div className="absolute inset-y-0 left-3 w-px sm:left-5">
      <div className="absolute inset-0 bg-ink-700" />
      <motion.div
        className="absolute inset-x-0 top-0 bg-gradient-to-b from-lamp/0 via-lamp to-lamp"
        style={{ height }}
      >
        <span className="absolute -bottom-1.5 left-1/2 size-3 -translate-x-1/2 rounded-full bg-lamp shadow-[0_0_16px_4px_rgba(233,162,59,0.55)]" />
        <motion.span className="absolute -bottom-1.5 left-4 font-mono text-[10px] text-lamp">
          {years}
        </motion.span>
      </motion.div>
    </div>
  );
}

function Slab({
  layer,
  index,
  progress,
  active,
}: {
  layer: (typeof LAYERS)[number];
  index: number;
  progress: MotionValue<number>;
  active: boolean;
}) {
  // Offsets must stay within [0, 1] and increase (scroll animations run natively).
  const start = index / LAYERS.length;
  const from = Math.max(0, start - 0.2);
  const to = Math.max(from + 0.01, start + 0.04);
  const x = useTransform(progress, [from, to], [index ? 40 : 0, 0]);
  const opacity = useTransform(progress, [from, to], [index ? 0.35 : 1, 1]);
  return (
    <motion.div
      style={{ x, opacity }}
      className={`relative overflow-hidden rounded-xl border bg-gradient-to-r p-3 transition-[border-color,box-shadow] sm:p-4 duration-500 ${layer.tone} ${
        active ? "border-lamp/60 shadow-[0_0_50px_-12px_rgba(233,162,59,0.5)]" : "border-ink-700"
      }`}
    >
      {/* sediment texture */}
      <div className="pointer-events-none absolute inset-0 opacity-30 [background:repeating-linear-gradient(0deg,transparent_0_6px,rgba(233,228,216,0.04)_6px_7px)]" />
      <div className="relative flex items-center justify-between gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-faint">
          {layer.title}
        </span>
        <span className={`font-mono text-[10px] ${active ? "text-lamp" : "text-faint"}`}>
          {layer.era}
        </span>
      </div>
      <div className="relative mt-2 truncate font-mono text-xs text-parchment/90 sm:mt-3 sm:text-[13px]">
        {layer.specimen}
      </div>
    </motion.div>
  );
}
