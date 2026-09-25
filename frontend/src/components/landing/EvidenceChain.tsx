"use client";

import { useRef } from "react";
import { motion, useScroll, useSpring, useTransform, type MotionValue } from "motion/react";
import {
  Braces,
  CircleDot,
  FileCode2,
  GitCommitHorizontal,
  GitPullRequest,
  Package,
} from "lucide-react";
import { Reveal } from "@/components/motion/primitives";

const CHAIN = [
  {
    icon: CircleDot,
    kind: "Issue",
    value: "#198",
    note: "Jobs lost when Redis restarts",
  },
  {
    icon: GitPullRequest,
    kind: "Pull request",
    value: "#214",
    note: "fixes #198",
  },
  {
    icon: GitCommitHorizontal,
    kind: "Commit",
    value: "4f2a9c1",
    note: "merged in #214",
  },
  { icon: FileCode2, kind: "File", value: "worker.py", note: "modified" },
  {
    icon: Braces,
    kind: "Function",
    value: "handle_failure()",
    note: "defined here",
  },
  { icon: Package, kind: "Dependency", value: "redis", note: "called through" },
];

export function EvidenceChain() {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start 0.75", "end 0.6"],
  });
  const progress = useSpring(scrollYProgress, { stiffness: 90, damping: 22 });

  return (
    <section id="chain" ref={ref} className="relative py-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <Reveal className="max-w-2xl">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-lamp">
            Chain of custody
          </p>
          <h2 className="mt-4 font-display text-4xl leading-[1.05] text-parchment sm:text-5xl">
            From the bug report to the line that fixed it.
          </h2>
          <p className="mt-5 leading-relaxed text-muted">
            A knowledge graph links issues, pull requests, commits, files, functions and the
            packages they use. Follow it in either direction: why this function exists, or what
            could break if you change it.
          </p>
        </Reveal>

        <ol className="relative mt-16 grid gap-6 md:grid-cols-6 md:gap-3">
          {/* connector: vertical on mobile, horizontal on desktop */}
          <div aria-hidden className="absolute top-0 bottom-0 left-6 w-px bg-ink-700 md:hidden">
            <motion.div className="h-full w-full origin-top bg-lamp" style={{ scaleY: progress }} />
          </div>
          <div
            aria-hidden
            className="absolute top-6 right-[8%] left-[8%] hidden h-px bg-ink-700 md:block"
          >
            <motion.div
              className="h-full w-full origin-left bg-lamp"
              style={{ scaleX: progress }}
            />
          </div>
          {CHAIN.map((node, i) => (
            <ChainNode key={node.kind} node={node} index={i} progress={progress} />
          ))}
        </ol>
      </div>
    </section>
  );
}

function ChainNode({
  node,
  index,
  progress,
}: {
  node: (typeof CHAIN)[number];
  index: number;
  progress: MotionValue<number>;
}) {
  const at = index / (CHAIN.length - 1);
  const lit = useTransform(progress, [Math.max(0, at - 0.12), Math.max(0.01, at)], [0, 1]);
  const opacity = useTransform(lit, [0, 1], [0.4, 1]);
  const y = useTransform(lit, [0, 1], [12, 0]);
  const glow = useTransform(lit, (v) => `0 0 ${24 * v}px ${2 * v}px rgba(233,162,59,${0.45 * v})`);
  const border = useTransform(lit, (v) => (v > 0.6 ? "rgba(233,162,59,0.7)" : "var(--ink-700)"));

  return (
    <motion.li
      style={{ opacity, y }}
      className="relative flex items-start gap-4 md:flex-col md:items-center md:text-center"
    >
      <motion.span
        style={{ boxShadow: glow, borderColor: border }}
        className="relative z-10 flex size-12 shrink-0 items-center justify-center rounded-2xl border bg-ink-900 text-lamp"
      >
        <node.icon className="size-5" />
      </motion.span>
      <div className="pt-1 md:pt-2">
        <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-faint">
          {node.kind}
        </div>
        <div className="mt-1 font-mono text-sm text-parchment">{node.value}</div>
        <div className="mt-0.5 text-xs text-muted">{node.note}</div>
      </div>
    </motion.li>
  );
}
