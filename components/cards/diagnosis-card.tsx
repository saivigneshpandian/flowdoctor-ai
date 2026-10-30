import { CircleUserRound, Wrench } from "lucide-react";

import { CardShell, StatusPill } from "@/components/cards/card-shell";
import type { DiagnosisMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

const TICKS = 24;

function ConfidenceBar({ confidence }: { confidence: number }) {
  const percent = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  const lit = Math.round((percent / 100) * TICKS);
  return (
    <div className="flex items-center gap-3">
      <div role="img" aria-label={`${percent}% confidence`} className="flex h-4 flex-1 items-stretch gap-[3px]">
        {Array.from({ length: TICKS }, (_, index) => (
          <span
            key={index}
            className={cn("flex-1 rounded-[1px]", index < lit ? "bg-primary" : "bg-secondary")}
          />
        ))}
      </div>
      <span aria-hidden className="font-mono text-sm font-medium">
        {percent}%
      </span>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="chart-label">{label}</dt>
      <dd className="mt-1 text-sm leading-relaxed">{children}</dd>
    </div>
  );
}

export function DiagnosisCard({ message }: { message: DiagnosisMessage }) {
  const autoFixable = message.category === "auto_fixable";
  return (
    <CardShell
      label="Diagnosis"
      tone="fail"
      badge={
        <StatusPill tone={autoFixable ? "ok" : "warn"}>
          {autoFixable ? <Wrench aria-hidden /> : <CircleUserRound aria-hidden />}
          {autoFixable ? "Auto-fixable" : "Needs your action"}
        </StatusPill>
      }
    >
      <p className="text-lg font-semibold tracking-tight">{message.workflow_name}</p>
      <p className="mt-0.5 font-mono text-xs text-fail">Failed at {message.failed_node}</p>

      <dl className="mt-4 grid gap-4">
        <Field label="Root cause">{message.root_cause}</Field>
        <Field label="Business impact">{message.impact}</Field>
        <Field label="Recommended solution">{message.recommendation}</Field>
        <div>
          <dt className="chart-label mb-1.5">Confidence</dt>
          <dd>
            <ConfidenceBar confidence={message.confidence} />
          </dd>
        </div>
      </dl>
    </CardShell>
  );
}
