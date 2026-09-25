"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import { motion } from "motion/react";
import { Logo } from "@/components/Logo";
import { EASE } from "@/components/motion/primitives";
import { AuthArt } from "./AuthArt";
import { auth } from "@/lib/api";
import { useAuth } from "./AuthProvider";

/** Only same-site paths are followed after login, never another origin. */
function nextPath(): string {
  const next = new URLSearchParams(window.location.search).get("next") ?? "/app";
  return next.startsWith("/") && !next.startsWith("//") && next !== "/" ? next : "/app";
}

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const { user, setUser } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signupEnabled, setSignupEnabled] = useState(true);
  const signup = mode === "signup";

  useEffect(() => {
    if (user) router.replace(nextPath());
  }, [user, router]);

  useEffect(() => {
    auth
      .config()
      .then((c) => setSignupEnabled(c.signup_enabled))
      .catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setUser(
        signup
          ? await auth.signup(email.trim(), password, name.trim())
          : await auth.login(email.trim(), password),
      );
    } catch (err) {
      const message = (err as Error).message;
      setError(message === "Failed to fetch" ? "Can't reach the API on port 8000." : message);
    } finally {
      setBusy(false);
    }
  }

  const field =
    "h-11 w-full rounded-lg border border-ink-700 bg-ink-900 px-3 text-sm outline-none transition-all duration-200 placeholder:text-faint hover:border-ink-600 focus:border-lamp/60 focus:shadow-[0_0_0_4px_rgba(233,162,59,0.12)]";

  return (
    <main className="grid min-h-full lg:grid-cols-2">
      <div className="relative flex flex-col px-4 py-6 sm:px-10">
        <Logo />
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: EASE }}
          className="m-auto w-full max-w-sm py-12"
        >
          <h1 className="font-display text-5xl leading-[1.02] text-parchment">
            {signup ? "Open a field journal" : "Back to the dig"}
          </h1>
          <p className="mt-2 text-sm text-muted">
            {signup
              ? "Create an account to keep your sites and case files."
              : "Sign in to your sites and case files."}
          </p>

          <form onSubmit={submit} className="mt-8 space-y-3">
            {signup ? (
              <label className="block">
                <span className="mb-1 block text-xs text-muted">Name (optional)</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  maxLength={100}
                  className={field}
                />
              </label>
            ) : null}
            <label className="block">
              <span className="mb-1 block text-xs text-muted">Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                className={field}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-muted">Password</span>
              <input
                type="password"
                required
                minLength={signup ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={signup ? "new-password" : "current-password"}
                className={field}
              />
              {signup ? (
                <span className="mt-1 block text-xs text-faint">At least 8 characters.</span>
              ) : null}
            </label>

            {error ? (
              <motion.p
                role="alert"
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: [0, -4, 4, -2, 0] }}
                transition={{ duration: 0.4 }}
                className="text-sm text-danger"
              >
                {error}
              </motion.p>
            ) : null}
            {signup && !signupEnabled ? (
              <p className="text-sm text-danger">Sign-up is disabled on this server.</p>
            ) : null}

            <button
              type="submit"
              disabled={busy || (signup && !signupEnabled)}
              className="btn-shimmer group flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-lamp text-sm font-semibold text-ink-950 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              {signup ? "Create account" : "Sign in"}
              {busy ? null : (
                <ArrowRight className="size-4 transition-transform duration-200 group-hover:translate-x-0.5" />
              )}
            </button>
          </form>

          <p className="mt-6 text-sm text-muted">
            {signup ? "Already have an account? " : "New here? "}
            <Link
              href={signup ? "/login" : "/signup"}
              className="text-lamp underline-offset-4 hover:underline"
            >
              {signup ? "Sign in" : "Create an account"}
            </Link>
          </p>
        </motion.div>
      </div>
      <AuthArt />
    </main>
  );
}
