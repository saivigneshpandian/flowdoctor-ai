import { CircleAlert, CircleCheck, CircleX } from "lucide-react";

import { CardShell, StatusPill, type CardTone } from "@/components/cards/card-shell";
import type { RepairResult } from "@/lib/types";
import { cn } from "@/lib/utils";

function Readout({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="bg-card px-3.5 py-3">
      <dt className="chart-label">{label}</dt>
      <dd className={cn("mt-1 font-mono text-sm font-medium first-letter:uppercase", tone)}>{value}</dd>
    </div>
  );
}

export function ResultCard({ result }: { result: RepairResult }) {
  const succeeded = result.repair_status === "success";
  // A repair only counts as fully healthy once it has been verified.
  const tone: CardTone = !succeeded ? "fail" : result.verified ? "ok" : "warn";
  const heading = !succeeded
    ? "Repair failed"
    : result.verified
      ? "Repair verified"
      : "Repair applied, not verified";

  return (
    <CardShell
      label="Repair result"
      tone={tone}
      badge={
        <StatusPill tone={tone}>
          {tone === "ok" && <CircleCheck aria-hidden />}
          {tone === "warn" && <CircleAlert aria-hidden />}
          {tone === "fail" && <CircleX aria-hidden />}
          {heading}
        </StatusPill>
      }
    >
      <p className="text-sm leading-relaxed">{result.message}</p>
      <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-4">
        <Readout
          label="Repair"
          value={result.repair_status}
          tone={succeeded ? "text-ok" : "text-fail"}
        />
        <Readout label="Execution" value={result.execution_status} />
        <Readout
          label="Recovered"
          value={`${result.recovered_count} ${result.recovered_count === 1 ? "execution" : "executions"}`}
        />
        <Readout
          label="Verification"
          value={result.verified ? "Verified" : "Not verified"}
          tone={result.verified ? "text-ok" : "text-warn"}
        />
      </dl>
    </CardShell>
  );
}
