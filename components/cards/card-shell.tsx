import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type CardTone = "ok" | "fail" | "warn" | "neutral";

const EDGE: Record<CardTone, string> = {
  ok: "before:bg-ok",
  fail: "before:bg-fail",
  warn: "before:bg-warn",
  neutral: "before:bg-ring/50",
};

interface CardShellProps {
  label: string;
  tone: CardTone;
  badge?: ReactNode;
  children: ReactNode;
}

// Shared chart-style frame: colored edge, mono section label, optional status on the right.
export function CardShell({ label, tone, badge, children }: CardShellProps) {
  return (
    <article
      className={cn(
        "relative overflow-hidden rounded-xl border bg-card before:absolute before:inset-y-0 before:left-0 before:w-[3px]",
        EDGE[tone],
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-2.5">
        <h3 className="chart-label">{label}</h3>
        {badge}
      </header>
      <div className="px-5 py-4">{children}</div>
    </article>
  );
}

const PILL: Record<CardTone, string> = {
  ok: "border-ok/35 bg-ok/10 text-ok",
  fail: "border-fail/35 bg-fail/10 text-fail",
  warn: "border-warn/35 bg-warn/10 text-warn",
  neutral: "border-border bg-secondary text-muted-foreground",
};

export function StatusPill({ tone, children }: { tone: CardTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5",
        PILL[tone],
      )}
    >
      {children}
    </span>
  );
}

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="mt-3 rounded-lg border border-fail/30 bg-fail/10 px-3 py-2 text-sm text-fail">
      {children}
    </p>
  );
}
