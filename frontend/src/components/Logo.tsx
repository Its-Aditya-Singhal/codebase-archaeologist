import Link from "next/link";

/** Strata cut through by a lamp beam: the Archaeologist mark. */
export function LogoMark({ className = "size-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect
        x="1"
        y="1"
        width="30"
        height="30"
        rx="8"
        fill="var(--ink-850)"
        stroke="var(--ink-600)"
      />
      <path
        d="M7 12h18"
        stroke="var(--parchment)"
        strokeOpacity=".9"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M7 17h13"
        stroke="var(--parchment)"
        strokeOpacity=".55"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M7 22h8"
        stroke="var(--parchment)"
        strokeOpacity=".3"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="23.5" cy="21.5" r="3" fill="var(--lamp)" />
    </svg>
  );
}

export function Logo({ href = "/", compact = false }: { href?: string; compact?: boolean }) {
  return (
    <Link
      href={href}
      className="group flex items-center gap-2.5 rounded-lg"
      aria-label="Codebase Archaeologist home"
    >
      <span className="transition-transform duration-300 ease-out group-hover:-rotate-6">
        <LogoMark />
      </span>
      {compact ? null : (
        <span className="hidden font-mono text-[11px] tracking-[0.22em] whitespace-nowrap text-parchment uppercase min-[420px]:inline">
          Codebase <span className="text-lamp">Archaeologist</span>
        </span>
      )}
    </Link>
  );
}
