"use client";

import Link from "next/link";
import { useRef } from "react";
import { ArrowDown, ArrowRight } from "lucide-react";
import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
} from "motion/react";
import { EASE } from "@/components/motion/primitives";
import { useAuth } from "@/components/auth/AuthProvider";
import { InvestigationDemo } from "./InvestigationDemo";

const LINE_1 = ["Find", "out"];
const LINE_2 = ["the", "code", "is", "the", "way", "it", "is."];

// Deterministic positions so server and client render the same motes.
const DUST = Array.from({ length: 22 }, (_, i) => ({
  left: (i * 37 + 11) % 100,
  top: 30 + ((i * 53) % 60),
  size: 1 + (i % 3),
  d: 8 + (i % 5) * 2.2,
  delay: -((i * 1.7) % 10),
}));

function Word({ children, i }: { children: React.ReactNode; i: number }) {
  return (
    <motion.span
      className="inline-block"
      initial={{ opacity: 0, y: 40, filter: "blur(10px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.9, ease: EASE, delay: 0.15 + i * 0.06 }}
    >
      {children}
    </motion.span>
  );
}

export function Hero() {
  const { user } = useAuth();
  const section = useRef<HTMLElement>(null);
  const demo = useRef<HTMLDivElement>(null);

  // Lamp light follows the cursor.
  const mx = useMotionValue(640);
  const my = useMotionValue(260);
  const sx = useSpring(mx, { stiffness: 90, damping: 20 });
  const sy = useSpring(my, { stiffness: 90, damping: 20 });
  const lamp = useMotionTemplate`radial-gradient(520px circle at ${sx}px ${sy}px, rgba(233,162,59,0.13), transparent 65%)`;

  // The demo window tilts up into place as it scrolls in.
  const { scrollYProgress } = useScroll({
    target: demo,
    offset: ["start end", "center center"],
  });
  const rotateX = useTransform(scrollYProgress, [0, 1], [22, 0]);
  const scale = useTransform(scrollYProgress, [0, 1], [0.9, 1]);
  const demoOpacity = useTransform(scrollYProgress, [0, 0.5], [0.35, 1]);

  // The headline drifts up and fades as the page scrolls away.
  const { scrollYProgress: leave } = useScroll({
    target: section,
    offset: ["start start", "end start"],
  });
  const headY = useTransform(leave, [0, 1], [0, -120]);
  const headOpacity = useTransform(leave, [0, 0.45], [1, 0]);

  return (
    <section
      ref={section}
      className="grain relative overflow-hidden pt-36 pb-24 sm:pt-44"
      onPointerMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        mx.set(e.clientX - r.left);
        my.set(e.clientY - r.top);
      }}
    >
      <div aria-hidden className="survey-grid grid-fade absolute inset-0" />
      <motion.div aria-hidden className="absolute inset-0" style={{ background: lamp }} />
      <div aria-hidden className="pointer-events-none absolute inset-0">
        {DUST.map((p, i) => (
          <span
            key={i}
            className="dust absolute rounded-full bg-lamp/70"
            style={
              {
                left: `${p.left}%`,
                top: `${p.top}%`,
                width: p.size,
                height: p.size,
                "--d": `${p.d}s`,
                "--delay": `${p.delay}s`,
              } as React.CSSProperties
            }
          />
        ))}
      </div>

      <motion.div
        style={{ y: headY, opacity: headOpacity }}
        className="relative mx-auto max-w-5xl px-4 text-center sm:px-6"
      >
        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: EASE }}
          className="mx-auto inline-flex items-center gap-2 rounded-full border border-ink-700 bg-ink-900/70 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.18em] text-muted backdrop-blur"
        >
          <span className="pulse-dot size-1.5 rounded-full bg-lamp" />
          Evidence-first code investigation
        </motion.p>

        <h1 className="mt-7 font-display text-[clamp(2.9rem,8vw,6.5rem)] leading-[0.98] tracking-tight text-parchment">
          <span className="sr-only">Find out why the code is the way it is.</span>
          <span aria-hidden>
            {LINE_1.map((w, i) => (
              <Word key={w} i={i}>
                {w}&nbsp;
              </Word>
            ))}
            <Word i={2}>
              <em className="relative text-lamp">
                why
                <svg
                  viewBox="0 0 200 20"
                  preserveAspectRatio="none"
                  className="absolute -bottom-1 left-0 h-3 w-full"
                >
                  <motion.path
                    d="M3 14 C 50 4, 120 4, 197 12"
                    fill="none"
                    stroke="var(--lamp)"
                    strokeWidth="3"
                    strokeLinecap="round"
                    initial={{ pathLength: 0 }}
                    animate={{ pathLength: 1 }}
                    transition={{ duration: 0.9, ease: EASE, delay: 1.1 }}
                  />
                </svg>
              </em>
            </Word>
            <br />
            {LINE_2.map((w, i) => (
              <Word key={`${w}-${i}`} i={i + 3}>
                {w}
                {i < LINE_2.length - 1 ? " " : ""}
              </Word>
            ))}
          </span>
        </h1>

        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.75 }}
          className="mx-auto mt-7 max-w-2xl text-base leading-relaxed text-muted sm:text-lg"
        >
          Point it at any repository. It digs through the code, the commits, the pull requests and
          the issues behind them, then answers your questions with evidence you can open. Every
          claim cited. No guesses.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: EASE, delay: 0.9 }}
          className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row"
        >
          <Link
            href={user ? "/app" : "/signup"}
            className="btn-shimmer group flex h-12 items-center gap-2 rounded-xl bg-lamp px-6 text-sm font-semibold text-ink-950 shadow-[0_0_40px_-8px_rgba(233,162,59,0.7)] transition hover:brightness-110"
          >
            {user ? "Open your sites" : "Start excavating, free"}
            <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-1" />
          </Link>
          <a
            href="#layers"
            className="group flex h-12 items-center gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-6 text-sm text-parchment backdrop-blur transition-colors duration-200 hover:border-ink-600 hover:bg-ink-850"
          >
            See how it digs
            <ArrowDown className="size-4 transition-transform duration-200 group-hover:translate-y-0.5" />
          </a>
        </motion.div>
      </motion.div>

      <div className="relative mx-auto mt-20 max-w-6xl px-4 sm:px-6 [perspective:1400px]">
        <motion.div
          ref={demo}
          style={{
            rotateX,
            scale,
            opacity: demoOpacity,
            transformOrigin: "50% 0%",
          }}
        >
          <InvestigationDemo />
        </motion.div>
        <div
          aria-hidden
          className="absolute inset-x-10 -bottom-10 -z-10 h-40 rounded-full bg-lamp/10 blur-3xl"
        />
      </div>
    </section>
  );
}
