"use client";

import { Check, Copy, Loader2, LogOut } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";

type CopyState = "idle" | "copied" | "failed";

// A full page load on logout also clears the chat and workflows held in memory,
// so nothing from this account is left for whoever logs in next.
export async function logOutAndReload() {
  await api.logout();
  window.location.assign("/login");
}

export function UserMenu() {
  const { state } = useAuth();
  const [copy, setCopy] = useState<CopyState>("idle");
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.status !== "authenticated") return null;
  const { email, webhook_url: webhookUrl } = state.user;

  async function copyWebhookUrl() {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
    setTimeout(() => setCopy("idle"), 2000);
  }

  async function logOut() {
    if (leaving) return;
    setLeaving(true);
    setError(null);
    try {
      await logOutAndReload();
    } catch (cause) {
      setError(errorMessage(cause));
      setLeaving(false);
    }
  }

  return (
    <div className="border-t px-4 py-3.5">
      <p className="truncate text-sm font-medium" title={email}>
        {email}
      </p>

      <p className="chart-label mt-3">Error Handler URL</p>
      <div className="mt-1 flex items-start gap-1.5">
        <code className="min-w-0 flex-1 font-mono text-[11px] leading-relaxed break-all text-muted-foreground">
          {webhookUrl}
        </code>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Copy the Error Handler URL"
          onClick={() => void copyWebhookUrl()}
          className="size-7 shrink-0"
        >
          {copy === "copied" ? (
            <Check aria-hidden className="size-3.5 text-ok" />
          ) : (
            <Copy aria-hidden className="size-3.5" />
          )}
        </Button>
      </div>
      <p aria-live="polite" className="min-h-4 text-[11px] text-muted-foreground">
        {copy === "copied" && "Copied."}
        {copy === "failed" && "Couldn't copy. Select the URL and copy it by hand."}
      </p>

      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={leaving}
        onClick={() => void logOut()}
        className="mt-1.5 w-full gap-1.5"
      >
        {leaving ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <LogOut aria-hidden className="size-3.5" />
        )}
        Log out
      </Button>
      {error && (
        <p role="alert" className="mt-2 text-xs text-fail">
          {error}
        </p>
      )}
    </div>
  );
}
