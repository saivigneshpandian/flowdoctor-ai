"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { AuthLoading, AuthProblem, useAuth } from "@/components/auth-provider";

// Only renders the app for a logged-in user who has connected n8n.
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { state, refresh } = useAuth();

  const redirect =
    state.status === "anonymous"
      ? "/login"
      : state.status === "authenticated" && !state.user.has_connection
        ? "/"
        : null;

  useEffect(() => {
    if (redirect) router.replace(redirect);
  }, [redirect, router]);

  if (state.status === "authenticated" && !redirect) return children;

  return (
    <main className="ecg-paper flex min-h-dvh flex-1 items-center justify-center px-6">
      {state.status === "error" ? (
        <AuthProblem message={state.message} onRetry={() => void refresh()} />
      ) : (
        <AuthLoading />
      )}
    </main>
  );
}
