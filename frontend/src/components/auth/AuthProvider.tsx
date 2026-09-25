"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { auth, AUTH_PAGES, SESSION_EXPIRED, type User } from "@/lib/api";

interface AuthState {
  user: User | null;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** Loads the session once, and keeps logged-out visitors on the login page. */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const publicPage = AUTH_PAGES.includes(pathname);

  useEffect(() => {
    auth
      .me()
      .then(setUser)
      .catch(() => setUnreachable(true))
      .finally(() => setChecked(true));
  }, []);

  // Expired or revoked session: back to the login page (via the effect below).
  useEffect(() => {
    const expire = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED, expire);
    return () => window.removeEventListener(SESSION_EXPIRED, expire);
  }, []);

  useEffect(() => {
    if (checked && !unreachable && !user && !publicPage) {
      const next = window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [checked, unreachable, user, publicPage, router]);

  const logout = useCallback(async () => {
    await auth.logout();
    setUser(null);
    router.replace("/login");
  }, [router]);

  let content = children;
  if (!publicPage && unreachable) {
    content = (
      <Centered>
        <p className="text-sm text-danger">
          Can&apos;t reach the API. Is the backend running on port 8000?
        </p>
      </Centered>
    );
  } else if (!publicPage && !user) {
    content = (
      <Centered>
        <Loader2 className="size-5 animate-spin text-faint" aria-label="Loading" />
      </Centered>
    );
  }

  return (
    <AuthContext.Provider value={{ user, setUser, logout }}>{content}</AuthContext.Provider>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center bg-ink-950">{children}</div>;
}
