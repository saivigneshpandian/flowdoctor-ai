import { Check, CircleUserRound, Loader2 } from "lucide-react";

import { CardShell, InlineError, StatusPill } from "@/components/cards/card-shell";
import { Button } from "@/components/ui/button";
import type { InstructionsMessage } from "@/lib/types";

export type InstructionStatus = "waiting" | "submitting" | "done" | "closed";

interface InstructionCardProps {
  message: InstructionsMessage;
  status: InstructionStatus;
  error?: string;
  onDone: () => void;
}

export function InstructionCard({ message, status, error, onDone }: InstructionCardProps) {
  const done = status === "done";
  const closed = status === "closed";
  return (
    <CardShell
      label="Your action needed"
      tone={done ? "ok" : closed ? "neutral" : "warn"}
      badge={
        <StatusPill tone={done ? "ok" : closed ? "neutral" : "warn"}>
          {done ? <Check aria-hidden /> : <CircleUserRound aria-hidden />}
          {done
            ? "Completed"
            : closed
              ? "No longer open"
              : status === "submitting"
                ? "Checking"
                : "Waiting on you"}
        </StatusPill>
      }
    >
      <p className="text-lg font-semibold tracking-tight">{message.title}</p>
      <ol className="mt-3 grid gap-2.5">
        {message.steps.map((step, index) => (
          <li key={index} className="grid grid-cols-[1.75rem_1fr] gap-2.5 text-sm leading-relaxed">
            <span className="flex size-6 items-center justify-center rounded-full border bg-secondary font-mono text-xs text-muted-foreground">
              {index + 1}
            </span>
            <span>{step}</span>
          </li>
        ))}
      </ol>

      {error && <InlineError>{error}</InlineError>}

      <Button
        type="button"
        disabled={status !== "waiting"}
        onClick={onDone}
        className="mt-4 h-10 gap-2 px-4"
      >
        {status === "submitting" ? (
          <Loader2 aria-hidden className="size-4 animate-spin" />
        ) : (
          <Check aria-hidden className="size-4" />
        )}
        I&apos;ve completed this
      </Button>
    </CardShell>
  );
}
