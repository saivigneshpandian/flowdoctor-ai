import { cn } from "@/lib/utils";
import type { WorkflowStatus } from "@/lib/types";

const STYLES: Record<WorkflowStatus, { color: string; label: string }> = {
  healthy: { color: "bg-ok", label: "Healthy" },
  failing: { color: "bg-fail", label: "Failing" },
  pending: { color: "bg-warn", label: "Pending approval" },
};

export function StatusDot({ status, className }: { status: WorkflowStatus; className?: string }) {
  const { color, label } = STYLES[status];
  return (
    <span className={cn("relative inline-flex size-2.5 shrink-0", className)}>
      {status !== "healthy" && (
        <span aria-hidden className={cn("status-ping absolute inset-0 rounded-full", color)} />
      )}
      <span aria-hidden className={cn("relative size-2.5 rounded-full", color)} />
      <span className="sr-only">{label}</span>
    </span>
  );
}
