"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { motion, useMotionValueEvent, useScroll, useSpring } from "motion/react";
import { Logo } from "@/components/Logo";
import { useAuth } from "@/components/auth/AuthProvider";

const LINKS = [
  { href: "#layers", label: "Layers" },
  { href: "#chain", label: "Evidence chain" },
  { href: "#features", label: "Features" },
  { href: "#how", label: "How it works" },
];

export function Nav() {
  const { user } = useAuth();
  const { scrollY, scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, {
    stiffness: 120,
    damping: 30,
    mass: 0.3,
  });
  const [scrolled, setScrolled] = useState(false);
  useMotionValueEvent(scrollY, "change", (y) => setScrolled(y > 24));

  return (
    <>
      <motion.div
        aria-hidden
        className="fixed inset-x-0 top-0 z-50 h-0.5 origin-left bg-lamp"
        style={{ scaleX: progress }}
      />
      <header className="fixed inset-x-0 top-3 z-40 px-4">
        <nav
          aria-label="Main"
          className={`mx-auto flex h-14 max-w-6xl items-center gap-6 rounded-2xl border px-4 transition-all duration-300 ease-out ${
            scrolled
              ? "border-ink-700 bg-ink-900/75 shadow-[0_8px_40px_-12px_rgba(0,0,0,0.8)] backdrop-blur-xl"
              : "border-transparent bg-transparent"
          }`}
        >
          <Logo />
          <ul className="ml-auto hidden items-center gap-6 lg:flex">
            {LINKS.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  className="relative text-sm text-muted transition-colors duration-200 after:absolute after:-bottom-1 after:left-0 after:h-px after:w-full after:origin-left after:scale-x-0 after:bg-lamp after:transition-transform after:duration-300 hover:text-parchment hover:after:scale-x-100"
                >
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="ml-auto flex items-center gap-2 lg:ml-2">
            {user ? (
              <Link
                href="/app"
                className="btn-shimmer group flex h-9 items-center gap-1.5 rounded-lg bg-lamp px-3.5 text-sm font-medium whitespace-nowrap text-ink-950 transition hover:brightness-110"
              >
                Open your sites
                <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
              </Link>
            ) : (
              <>
                <Link
                  href="/login"
                  className="hidden h-9 items-center rounded-lg px-3 text-sm text-muted transition-colors hover:text-parchment sm:flex"
                >
                  Sign in
                </Link>
                <Link
                  href="/signup"
                  className="btn-shimmer group flex h-9 items-center gap-1.5 rounded-lg bg-lamp px-3.5 text-sm font-medium whitespace-nowrap text-ink-950 transition hover:brightness-110"
                >
                  Get started
                  <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
                </Link>
              </>
            )}
          </div>
        </nav>
      </header>
    </>
  );
}
