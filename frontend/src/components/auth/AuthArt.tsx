"use client";

import { motion } from "motion/react";
import { EASE } from "@/components/motion/primitives";

const LAYERS = [
  {
    label: "code",
    text: "def handle_failure(self, job, exc):",
    tone: "bg-[#2b2f37]",
  },
  {
    label: "commit",
    text: "4f2a9c1  Retry failed jobs with backoff",
    tone: "bg-[#2d2a25]",
  },
  {
    label: "pull request",
    text: "#214  Add backoff to worker retries",
    tone: "bg-[#2e2720]",
  },
  {
    label: "issue",
    text: "#198  Jobs lost when Redis restarts",
    tone: "bg-[#2f241c]",
  },
];

/** Decorative side panel for the auth pages: strata settling into place. */
export function AuthArt() {
  return (
    <aside
      aria-hidden
      className="grain relative hidden overflow-hidden border-l border-ink-700 bg-ink-900 lg:flex lg:flex-col lg:justify-center lg:px-14"
    >
      <div className="survey-grid grid-fade absolute inset-0" />
      <div className="breathe absolute top-1/3 left-1/2 size-[420px] -translate-x-1/2 rounded-full bg-lamp/12 blur-[100px]" />
      <div className="relative space-y-3">
        {LAYERS.map((l, i) => (
          <motion.div
            key={l.label}
            initial={{ opacity: 0, x: 60 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.9, ease: EASE, delay: 0.2 + i * 0.12 }}
            whileHover={{ x: -6 }}
            className={`rounded-xl border border-ink-700 p-4 ${l.tone}`}
            style={{ marginLeft: `${i * 5}%` }}
          >
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-faint">
              {l.label}
            </div>
            <div className="mt-2 truncate font-mono text-[13px] text-parchment/90">{l.text}</div>
          </motion.div>
        ))}
      </div>
      <motion.blockquote
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, ease: EASE, delay: 0.8 }}
        className="relative mt-12 max-w-md"
      >
        <p className="font-display text-3xl leading-tight text-parchment">
          Code tells you <em className="text-lamp">what</em>. Its history tells you why.
        </p>
        <p className="mt-3 text-sm text-muted">
          Every answer traced back to the commits, reviews and issues behind it.
        </p>
      </motion.blockquote>
    </aside>
  );
}
