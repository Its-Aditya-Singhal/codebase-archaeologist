import type { RepoStatus } from "@/lib/api";

const STYLES: Record<RepoStatus, string> = {
  queued: "text-muted border-ink-600",
  cloning: "text-lamp border-lamp/40",
  parsing: "text-lamp border-lamp/40",
  embedding: "text-lamp border-lamp/40",
  ready: "text-evidence border-evidence/40",
  failed: "text-danger border-danger/40",
};

export function StatusBadge({ status }: { status: RepoStatus }) {
  return (
    <span
      className={`rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}
