"use client";

import { Stethoscope, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";
import type { WorkflowError } from "@/lib/types";

interface WorkflowErrorPanelProps {
  workflowId: string;
  // A change in the count means a newer failure, or one that was recovered.
  failedCount: number;
  diagnoseDisabled: boolean;
  onDiagnose: () => void;
}

type Loaded = { key: string; error: WorkflowError } | { key: string; problem: string };

// n8n's own error, shown as recorded. Nothing here calls the AI until Diagnose is pressed.
export function WorkflowErrorPanel({
  workflowId,
  failedCount,
  diagnoseDisabled,
  onDiagnose,
}: WorkflowErrorPanelProps) {
  const key = `${workflowId}:${failedCount}`;
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getWorkflowError(workflowId)
      .then((error) => {
        if (!cancelled) setLoaded({ key, error });
      })
      .catch((cause: unknown) => {
        if (!cancelled) setLoaded({ key, problem: errorMessage(cause) });
      });
    return () => {
      cancelled = true;
    };
  }, [key, workflowId]);

  const current = loaded?.key === key ? loaded : null;

  return (
    <div className="mx-1 mt-1 mb-1.5 rounded-lg border border-fail/25 bg-fail/5 p-3">
      <div aria-live="polite">
        {!current && (
          <div aria-label="Loading the n8n error" className="grid gap-2">
            <Skeleton className="h-3 w-1/2 bg-secondary" />
            <Skeleton className="h-3 w-full bg-secondary" />
            <Skeleton className="h-3 w-4/5 bg-secondary" />
          </div>
        )}
        {current && "error" in current && (
          <>
            <p className="chart-label">Failed node</p>
            <p className="mt-0.5 text-xs font-medium break-words text-fail">
              {current.error.failed_node}
            </p>
            <p className="chart-label mt-2.5">n8n error</p>
            <p className="mt-0.5 font-mono text-[11px] leading-relaxed break-words text-foreground/90">
              {current.error.error_message}
            </p>
          </>
        )}
        {current && "problem" in current && (
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <TriangleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
            {current.problem}
          </p>
        )}
      </div>

      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={diagnoseDisabled}
        onClick={onDiagnose}
        className="mt-3 w-full gap-1.5"
      >
        <Stethoscope aria-hidden className="size-3.5" />
        Diagnose
      </Button>
    </div>
  );
}
