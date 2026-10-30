"use client";

import { CircleX, Info, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { AuthLoading, useAuth } from "@/components/auth-provider";
import { EcgTrace } from "@/components/ecg-trace";
import { Logo, MockBadge } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";

const DEMO_HINT = process.env.NEXT_PUBLIC_DEMO_HINT === "true";
const DEMO_EMAIL = process.env.NEXT_PUBLIC_DEMO_EMAIL;
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD;

const COPY = {
  login: {
    label: "Welcome back",
    heading: "Log in to see your workflows' vitals.",
    submit: "Log in",
    busy: "Logging in",
    prompt: "New to FlowDoctor?",
    other: { href: "/signup", label: "Create an account" },
  },
  signup: {
    label: "On call for your n8n workflows",
    heading: "Create an account. We'll take it from there.",
    submit: "Create account",
    busy: "Creating account",
    prompt: "Already have an account?",
    other: { href: "/login", label: "Log in" },
  },
};

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const { state, setUser } = useAuth();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copy = COPY[mode];

  const destination =
    state.status === "authenticated" ? (state.user.has_connection ? "/dashboard" : "/") : null;

  useEffect(() => {
    if (destination) router.replace(destination);
  }, [destination, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    const form = new FormData(event.currentTarget);
    const credentials = {
      email: String(form.get("email") ?? "").trim(),
      password: String(form.get("password") ?? ""),
    };

    setSubmitting(true);
    setError(null);
    try {
      setUser(
        await (mode === "login"
          ? api.login(credentials)
          : api.signup({ ...credentials, invite_code: String(form.get("invite_code") ?? "") })),
      );
    } catch (cause) {
      setError(errorMessage(cause));
      setSubmitting(false);
    }
  }

  return (
    <main className="ecg-paper relative flex min-h-dvh flex-1 flex-col overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-24 opacity-70">
        <EcgTrace tone="ok" />
      </div>

      <header className="relative flex items-center justify-between gap-3 px-6 py-5 lg:px-10">
        <Logo />
        <MockBadge />
      </header>

      <div className="relative mx-auto grid w-full max-w-6xl flex-1 items-center gap-10 px-6 pb-12 lg:grid-cols-[1fr_27rem] lg:gap-16 lg:px-10">
        <div className="max-w-xl">
          <p className="chart-label">{copy.label}</p>
          <h1 className="mt-3 text-4xl leading-[1.08] font-semibold tracking-tight text-balance sm:text-5xl lg:text-6xl">
            {copy.heading}
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-muted-foreground">
            See workflow health, ask why something failed, and review each proposed fix. The agent
            never changes anything without your approval.
          </p>
        </div>

        <section
          aria-label={copy.submit}
          className="rounded-2xl border bg-card/95 p-6 shadow-[0_24px_70px_-30px_rgb(0_0_0/80%)] backdrop-blur-sm sm:p-7"
        >
          {state.status === "loading" || destination ? (
            <AuthLoading />
          ) : (
            <form onSubmit={handleSubmit} noValidate className="grid gap-5">
              {mode === "login" && DEMO_HINT && (
                <div className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2.5 text-sm">
                  <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                  <div className="min-w-0">
                    <p className="font-medium">Try the demo account</p>
                    {DEMO_EMAIL && DEMO_PASSWORD ? (
                      <p className="mt-0.5 font-mono text-xs break-all text-muted-foreground">
                        {DEMO_EMAIL} / {DEMO_PASSWORD}
                      </p>
                    ) : (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Ask the team for the demo login.
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div className="grid gap-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  inputMode="email"
                  required
                  autoComplete="email"
                  placeholder="you@company.com"
                  className="h-11 text-sm"
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  required
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  placeholder={mode === "login" ? "Your password" : "At least 8 characters"}
                  className="h-11 text-sm"
                />
              </div>

              {mode === "signup" && (
                <div className="grid gap-2">
                  <Label htmlFor="invite_code">Invite code</Label>
                  <Input
                    id="invite_code"
                    name="invite_code"
                    required
                    autoComplete="off"
                    placeholder="From your invitation"
                    className="h-11 font-mono text-sm"
                  />
                </div>
              )}

              <Button type="submit" disabled={submitting} className="mt-1 h-11 gap-2 text-sm">
                {submitting && <Loader2 aria-hidden className="size-4 animate-spin" />}
                {submitting ? copy.busy : copy.submit}
              </Button>

              <div aria-live="polite">
                {error && (
                  <p
                    role="alert"
                    className="flex items-start gap-2 rounded-lg border border-fail/30 bg-fail/10 px-3 py-2.5 text-sm text-fail"
                  >
                    <CircleX aria-hidden className="mt-0.5 size-4 shrink-0" />
                    {error}
                  </p>
                )}
              </div>

              <p className="text-sm text-muted-foreground">
                {copy.prompt}{" "}
                <Link
                  href={copy.other.href}
                  className="text-foreground underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {copy.other.label}
                </Link>
              </p>
            </form>
          )}
        </section>
      </div>
    </main>
  );
}
