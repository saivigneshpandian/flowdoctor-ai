import { relativeTime } from "@/lib/format";
import type { Thread } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ThreadListItemProps {
  thread: Thread;
  active: boolean;
  onSelect: () => void;
}

export function ThreadListItem({ thread, active, onSelect }: ThreadListItemProps) {
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "w-full rounded-lg border border-transparent px-3 py-2 text-left transition-colors outline-none hover:bg-panel-raised focus-visible:ring-2 focus-visible:ring-ring",
        active && "border-border bg-panel-raised",
      )}
    >
      <span className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm">{thread.title}</span>
        {thread.pending && (
          <span className="shrink-0 rounded-full border border-warn/35 bg-warn/10 px-1.5 text-[10px] font-medium text-warn">
            Needs you
          </span>
        )}
      </span>
      <span className="mt-0.5 flex items-center justify-between gap-2 font-mono text-[11px] text-muted-foreground">
        <span className="min-w-0 truncate">{thread.workflow_name ?? "General"}</span>
        <span className="shrink-0">{relativeTime(thread.updated_at)}</span>
      </span>
    </button>
  );
}
