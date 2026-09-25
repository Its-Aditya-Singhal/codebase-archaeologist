"use client";

import { LogOut } from "lucide-react";
import { useAuth } from "./AuthProvider";

export function UserMenu({ compact = false }: { compact?: boolean }) {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span
        className={`truncate font-mono text-xs text-muted ${compact ? "hidden sm:inline" : ""}`}
        title={user.email}
      >
        {user.name || user.email}
      </span>
      <button
        onClick={logout}
        title="Sign out"
        className="flex items-center gap-1.5 rounded border border-ink-700 px-2 py-1 text-xs text-muted hover:border-ink-600 hover:text-parchment"
      >
        <LogOut className="size-3.5" />
        {compact ? null : "Sign out"}
      </button>
    </div>
  );
}
