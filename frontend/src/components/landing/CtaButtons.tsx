"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useAuth } from "@/components/auth/AuthProvider";

export function CtaButtons() {
  const { user } = useAuth();
  return (
    <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
      <Link
        href={user ? "/app" : "/signup"}
        className="btn-shimmer group flex h-12 items-center gap-2 rounded-xl bg-lamp px-6 text-sm font-semibold text-ink-950 shadow-[0_0_40px_-8px_rgba(233,162,59,0.7)] transition hover:brightness-110"
      >
        {user ? "Open your sites" : "Create a free account"}
        <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-1" />
      </Link>
      {user ? null : (
        <Link
          href="/login"
          className="flex h-12 items-center rounded-xl border border-ink-700 px-6 text-sm text-parchment transition-colors duration-200 hover:border-ink-600 hover:bg-ink-850"
        >
          I already have one
        </Link>
      )}
    </div>
  );
}
