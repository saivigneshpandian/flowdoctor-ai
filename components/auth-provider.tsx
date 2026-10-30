"use client";

import { CircleX, Loader2, RefreshCw } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { Me } from "@/lib/types";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: Me }
  | { status: "error"; message: string };

interface AuthValue {
  state: AuthState;
  refresh: () => Promise<void>;
  setUser: (user: Me) => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

// The session cookie is httpOnly and only the backend can validate it, so the app asks the API
// who is logged in.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      setState({ status: "authenticated", user: await api.me() });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setState({ status: "anonymous" });
      } else {
        setState({ status: "error", message: errorMessage(error) });
      }
    }
  }, []);

  useEffect(() => {
    const initial = setTimeout(() => void refresh(), 0);
    return () => clearTimeout(initial);
  }, [refresh]);

  const value = useMemo(
    (): AuthValue => ({
      state,
      refresh,
      setUser: (user) => setState({ status: "authenticated", user }),
    }),
    [refresh, state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthLoading({ label = "Checking your session…" }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 py-16 text-sm text-muted-foreground">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      {label}
    </p>
  );
}

export function AuthProblem({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="max-w-md rounded-xl border border-fail/30 bg-fail/10 p-5">
      <p className="flex items-center gap-2 font-medium text-fail">
        <CircleX aria-hidden className="size-4" />
        Couldn&apos;t check your session
      </p>
      <p className="mt-1 text-sm text-muted-foreground">{message}</p>
      <Button type="button" variant="outline" className="mt-3 h-9 gap-2" onClick={onRetry}>
        <RefreshCw aria-hidden className="size-4" /> Retry
      </Button>
    </div>
  );
}
