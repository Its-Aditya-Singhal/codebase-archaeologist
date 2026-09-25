import {
  Box,
  CircleDot,
  FileCode2,
  GitCommitHorizontal,
  GitPullRequest,
  Package,
  User,
  type LucideIcon,
} from "lucide-react";
import type { GraphNode } from "@/lib/api";

export const NODE_ICON: Record<GraphNode["kind"], LucideIcon> = {
  file: FileCode2,
  symbol: Box,
  module: Package,
  dependency: Package,
  commit: GitCommitHorizontal,
  pull_request: GitPullRequest,
  issue: CircleDot,
  author: User,
};

export const KIND_GLYPH: Record<string, string> = {
  function: "ƒ",
  method: "m",
  class: "C",
  interface: "I",
  type: "T",
  struct: "S",
  enum: "E",
  trait: "T",
  impl: "impl",
  protocol: "P",
  module: "M",
  namespace: "N",
  section: "§",
};

/** Edge colour by the relation's direction: what leads *into* the target is
 *  lamp-amber, what the target reaches *out* to is evidence-teal. */
export const RELATION_TONE: Record<string, string> = {
  caller: "var(--lamp)",
  importer: "var(--lamp)",
  subclass: "var(--lamp)",
  callee: "var(--evidence)",
  imports: "var(--evidence)",
  base: "var(--evidence)",
  uses: "#a996e0",
  modifies: "var(--faint)",
  merged_in: "#a996e0",
  part_of: "#a996e0",
  fixes: "#a996e0",
  mentions: "#a996e0",
};

export const RELATION_LABEL: Record<string, string> = {
  caller: "Callers",
  importer: "Imported by",
  subclass: "Subclasses",
  callee: "Calls",
  imports: "Imports",
  base: "Inherits from",
  uses: "Packages used",
};

const TEST_PATH =
  /(^|\/)(tests?|__tests__|spec|specs|testing)(\/|$)|(^|\/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.[cm]?[jt]sx?$/;

export function isTestPath(path: string | null | undefined): boolean {
  return Boolean(path && TEST_PATH.test(path));
}

export function nodeSubtitle(n: GraphNode): string {
  if (n.kind === "symbol") return `${n.path}:${n.start_line}`;
  if (n.kind === "file") return n.path ?? "";
  if (n.kind === "dependency") return `declared ${n.data.ecosystem ?? ""} dependency`;
  if (n.kind === "module") return n.data.stdlib ? "standard library" : `external ${n.data.ecosystem ?? ""}`;
  if (n.kind === "commit") {
    const date = typeof n.data.date === "string" ? n.data.date.slice(0, 10) : "";
    return `${String(n.data.author ?? "")} · ${date}`;
  }
  if (n.kind === "pull_request" || n.kind === "issue") {
    return `${n.kind === "issue" ? "issue" : "PR"} · ${n.data.state ?? "not fetched"}`;
  }
  return "";
}

export function NodeGlyph({ node, className = "" }: { node: GraphNode; className?: string }) {
  if (node.kind === "symbol") {
    return (
      <span className={`w-4 shrink-0 text-center font-mono text-[10px] text-faint ${className}`}>
        {KIND_GLYPH[String(node.data.kind)] ?? "·"}
      </span>
    );
  }
  const Icon = NODE_ICON[node.kind];
  return <Icon className={`size-3.5 shrink-0 text-faint ${className}`} />;
}
