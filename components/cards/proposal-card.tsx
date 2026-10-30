import { Ban, Check, CircleDashed, CircleX, Loader2, X } from "lucide-react";

import { CardShell, InlineError, StatusPill, type CardTone } from "@/components/cards/card-shell";
import { CodeDiff } from "@/components/code-diff";
import { Button } from "@/components/ui/button";
import { buildDiffSections } from "@/lib/proposal-diff";
import type { ProposalMessage } from "@/lib/types";

export type ProposalStatus =
  | "awaiting"
  | "approving"
  | "rejecting"
  | "approved"
  | "rejected"
  | "failed"
  | "closed";

interface ProposalCardProps {
  message: ProposalMessage;
  status: ProposalStatus;
  error?: string;
  onApprove: () => void;
  onReject: () => void;
}

const BADGES: Record<ProposalStatus, { tone: CardTone; label: string }> = {
  awaiting: { tone: "warn", label: "Awaiting approval" },
  approving: { tone: "warn", label: "Applying fix" },
  rejecting: { tone: "warn", label: "Rejecting" },
  approved: { tone: "ok", label: "Approved" },
  rejected: { tone: "neutral", label: "Rejected" },
  failed: { tone: "fail", label: "Approved · repair failed" },
  closed: { tone: "neutral", label: "No longer open" },
};

const ICONS: Partial<Record<ProposalStatus, typeof Check>> = {
  awaiting: CircleDashed,
  approved: Check,
  rejected: X,
  failed: CircleX,
  closed: Ban,
};

export function ProposalCard({ message, status, error, onApprove, onReject }: ProposalCardProps) {
  const badge = BADGES[status];
  const inFlight = status === "approving" || status === "rejecting";
  const Icon = ICONS[status];

  return (
    <CardShell
      label="Repair proposal"
      tone={inFlight || status === "awaiting" ? "warn" : badge.tone}
      badge={
        <StatusPill tone={badge.tone}>
          {Icon ? <Icon aria-hidden /> : <Loader2 aria-hidden className="animate-spin" />}
          {badge.label}
        </StatusPill>
      }
    >
      <p className="text-lg font-semibold tracking-tight">{message.workflow_name}</p>
      <p className="mt-0.5 font-mono text-xs text-muted-foreground">Node: {message.node_name}</p>

      <div className="mt-4 grid gap-3">
        {buildDiffSections(message.before, message.after).map((section) => (
          <CodeDiff key={section.label} section={section} />
        ))}
      </div>

      <h4 className="chart-label mt-4">What changes</h4>
      <p className="mt-1 text-sm leading-relaxed">{message.explanation}</p>

      {error && <InlineError>{error}</InlineError>}

      <div className="mt-4 flex flex-wrap items-center gap-2.5">
          <Button
            type="button"
            disabled={status !== "awaiting"}
            onClick={onApprove}
            className="h-10 gap-2 bg-ok px-4 text-[#06281a] hover:bg-ok/85"
          >
            {status === "approving" ? (
              <Loader2 aria-hidden className="size-4 animate-spin" />
            ) : (
              <Check aria-hidden className="size-4" />
            )}
            Approve
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={status !== "awaiting"}
            onClick={onReject}
            className="h-10 gap-2 px-4"
          >
            {status === "rejecting" ? (
              <Loader2 aria-hidden className="size-4 animate-spin" />
            ) : (
              <X aria-hidden className="size-4" />
            )}
            Reject
          </Button>
          {status === "awaiting" && (
            <p className="text-xs text-muted-foreground">Nothing changes until you approve.</p>
          )}
      </div>
    </CardShell>
  );
}
