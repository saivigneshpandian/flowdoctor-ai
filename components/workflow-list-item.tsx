import { StatusDot } from "@/components/status-dot";
import { relativeTime } from "@/lib/format";
import type { Workflow } from "@/lib/types";
import { cn } from "@/lib/utils";

interface WorkflowListItemProps {
  workflow: Workflow;
  focused: boolean;
  onSelect: () => void;
}

export function WorkflowListItem({ workflow, focused, onSelect }: WorkflowListItemProps) {
  return (
    <button
      type="button"
      aria-pressed={focused}
      onClick={onSelect}
      className={cn(
        "grid w-full grid-cols-[auto_1fr] gap-x-3 rounded-lg border border-transparent px-3 py-2.5 text-left transition-colors outline-none hover:bg-panel-raised focus-visible:ring-2 focus-visible:ring-ring",
        focused && "border-border bg-panel-raised",
      )}
    >
      <StatusDot status={workflow.status} className="mt-1.5" />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{workflow.name}</span>
        <span className="mt-0.5 flex items-center justify-between gap-2 font-mono text-[11px] text-muted-foreground">
          <span>{relativeTime(workflow.last_execution_at)}</span>
          <span className={cn(workflow.failed_count > 0 && "text-fail")}>
            {workflow.failed_count} failed
          </span>
        </span>
      </span>
    </button>
  );
}
