"use client";

import { CircleCheck, CircleX, Loader2, Plug } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { AuthLoading, AuthProblem, useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { logOutAndReload } from "@/components/user-menu";
import { api, errorMessage, USE_MOCKS } from "@/lib/api";

type Status =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "connected" }
  | { state: "failed"; error: string };

export function ConnectForm() {
  const router = useRouter();
  const { state: auth, refresh } = useAuth();
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const [connectionChecked, setConnectionChecked] = useState(false);
  // The key stays in the uncontrolled DOM input and is wiped on submit, so it never enters React state.
  const apiKeyInput = useRef<HTMLInputElement>(null);

  const anonymous = auth.status === "anonymous";
  // Mocks always have workflows, so the form would never show if mock mode checked too.
  const hasConnection =
    !USE_MOCKS && auth.status === "authenticated" && auth.user.has_connection;

  useEffect(() => {
    if (anonymous) router.replace("/login");
  }, [anonymous, router]);

  // If this user already has a working connection, there's nothing to enter.
  useEffect(() => {
    if (!hasConnection) return;
    let cancelled = false;
    api
      .getWorkflows()
      .then(() => {
        if (!cancelled) router.replace("/dashboard");
      })
      .catch(() => {
        if (!cancelled) setConnectionChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, [hasConnection, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.state === "testing" || status.state === "connected") return;

    const form = new FormData(event.currentTarget);
    const n8nUrl = String(form.get("n8n_url") ?? "").trim();
    const apiKey = String(form.get("api_key") ?? "");
    const telegramChatId = String(form.get("telegram_chat_id") ?? "").trim();

    if (!/^https?:\/\/\S+$/i.test(n8nUrl)) {
      setStatus({ state: "failed", error: "Enter a full URL starting with http:// or https://." });
      return;
    }
    if (!apiKey) {
      setStatus({ state: "failed", error: "Enter your n8n API key." });
      return;
    }

    if (apiKeyInput.current) apiKeyInput.current.value = "";
    setStatus({ state: "testing" });

    try {
      const response = await api.connect({
        n8n_url: n8nUrl,
        api_key: apiKey,
        ...(telegramChatId ? { telegram_chat_id: telegramChatId } : {}),
      });
      if (response.ok) {
        setStatus({ state: "connected" });
        // The app's guard sends users without a connection back here, so update it first.
        await refresh();
        router.push("/dashboard");
      } else {
        setStatus({ state: "failed", error: response.error ?? "n8n refused the connection." });
      }
    } catch (error) {
      setStatus({ state: "failed", error: errorMessage(error) });
    }
  }

  const testing = status.state === "testing";
  const connected = status.state === "connected";

  if (auth.status === "error") {
    return <AuthProblem message={auth.message} onRetry={() => void refresh()} />;
  }
  if (auth.status !== "authenticated") return <AuthLoading />;
  if (hasConnection && !connectionChecked) {
    return <AuthLoading label="Checking your n8n connection…" />;
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="grid gap-5">
      <div className="grid gap-2">
        <Label htmlFor="n8n_url">n8n instance URL</Label>
        <Input
          id="n8n_url"
          name="n8n_url"
          type="url"
          inputMode="url"
          required
          autoComplete="url"
          placeholder="https://n8n.yourcompany.com"
          className="h-11 font-mono text-sm"
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="api_key">API key</Label>
        <Input
          ref={apiKeyInput}
          id="api_key"
          name="api_key"
          type="password"
          required
          autoComplete="off"
          placeholder="Paste your n8n API key"
          className="h-11 font-mono text-sm"
        />
        <p className="text-xs leading-relaxed text-muted-foreground">
          Sent once to connect, and never stored in this browser.
        </p>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="telegram_chat_id">
          Telegram chat ID <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="telegram_chat_id"
          name="telegram_chat_id"
          autoComplete="off"
          placeholder="123456789"
          className="h-11 font-mono text-sm"
        />
      </div>

      <Button type="submit" disabled={testing || connected} className="mt-1 h-11 gap-2 text-sm">
        {testing || connected ? (
          <Loader2 aria-hidden className="size-4 animate-spin" />
        ) : (
          <Plug aria-hidden className="size-4" />
        )}
        {testing ? "Testing connection" : connected ? "Opening dashboard" : "Test Connection"}
      </Button>

      <div aria-live="polite" className="min-h-5 text-sm">
        {testing && <p className="text-muted-foreground">Contacting your n8n instance…</p>}
        {connected && (
          <p className="flex items-center gap-2 text-ok">
            <CircleCheck aria-hidden className="size-4" /> Connected.
          </p>
        )}
        {status.state === "failed" && (
          <div role="alert" className="flex items-start gap-2 rounded-lg border border-fail/30 bg-fail/10 px-3 py-2.5 text-fail">
            <CircleX aria-hidden className="mt-0.5 size-4 shrink-0" />
            <div>
              <p>{status.error}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                For your security, re-enter the API key to try again.
              </p>
            </div>
          </div>
        )}
      </div>

      <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <span className="min-w-0 break-all">Signed in as {auth.user.email}.</span>
        <button
          type="button"
          onClick={() => void logOutAndReload().catch(() => window.location.assign("/login"))}
          className="underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          Log out
        </button>
      </p>
    </form>
  );
}
