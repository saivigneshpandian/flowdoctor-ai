"use client";

import { Activity, ClipboardList, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Logo, MockBadge } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ThreadListItem } from "@/components/thread-list-item";
import { WorkflowErrorPanel } from "@/components/workflow-error-panel";
import { UserMenu } from "@/components/user-menu";
import { WorkflowListItem } from "@/components/workflow-list-item";
import { useWorkspace } from "@/components/workspace-provider";
import { relativeTime } from "@/lib/format";
import type { Workflow } from "@/lib/types";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: Activity },
  { href: "/incidents", label: "Incidents", icon: ClipboardList },
];

export function AppSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    workflows,
    workflowsError,
    lastSyncedAt,
    refreshWorkflows,
    threads,
    threadsError,
    activeThread,
    focusedWorkflowId,
    openWorkflow,
    openThread,
    startNewChat,
    busy,
    sendMessage,
  } = useWorkspace();

  function goToChat() {
    if (pathname !== "/dashboard") router.push("/dashboard");
    onNavigate?.();
  }

  // The workflow's thread is already open, so this goes there.
  function diagnose() {
    void sendMessage("Why did this fail?");
    goToChat();
  }

  function selectWorkflow(workflow: Workflow) {
    if (focusedWorkflowId !== workflow.id) void openWorkflow(workflow);
    goToChat();
  }

  function selectThread(threadId: string) {
    if (activeThread?.id !== threadId) void openThread(threadId);
    goToChat();
  }

  function newChat() {
    startNewChat();
    goToChat();
  }

  return (
    <div className="flex h-full flex-col bg-panel">
      <div className="flex items-center justify-between gap-2 px-4 pt-4 pb-3">
        <Link href="/dashboard" onClick={onNavigate} className="rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Logo />
        </Link>
        <MockBadge />
      </div>

      <nav aria-label="Main" className="grid gap-0.5 px-2 pb-3">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors outline-none hover:bg-panel-raised hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                active && "bg-panel-raised font-medium text-foreground",
              )}
            >
              <Icon aria-hidden className="size-4" />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-3 pb-3">
        <Button type="button" variant="outline" size="sm" onClick={newChat} className="w-full gap-1.5">
          <Plus aria-hidden className="size-3.5" />
          New chat
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t px-2 pb-3">
        <div className="flex items-baseline justify-between px-3 pt-4 pb-2">
          <h2 className="chart-label">Workflows</h2>
          {workflows && <span className="font-mono text-[11px] text-muted-foreground">{workflows.length}</span>}
        </div>

        {workflows === null && !workflowsError && (
          <div aria-label="Loading workflows" className="grid gap-1">
            {[0, 1, 2, 3].map((index) => (
              <div key={index} className="grid grid-cols-[auto_1fr] gap-x-3 px-3 py-2.5">
                <Skeleton className="mt-1.5 size-2.5 rounded-full bg-secondary" />
                <div className="grid gap-2">
                  <Skeleton className="h-4 w-4/5 bg-secondary" />
                  <Skeleton className="h-3 w-full bg-secondary" />
                </div>
              </div>
            ))}
          </div>
        )}

        {workflows === null && workflowsError && (
          <div role="alert" className="m-1 rounded-lg border border-fail/30 bg-fail/10 p-3">
            <p className="flex items-center gap-2 text-sm font-medium text-fail">
              <TriangleAlert aria-hidden className="size-4" />
              Couldn&apos;t load workflows
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{workflowsError}</p>
            <div className="mt-2.5 flex items-center gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => void refreshWorkflows()}>
                <RefreshCw aria-hidden /> Retry
              </Button>
              <Link href="/" className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                Check connection
              </Link>
            </div>
          </div>
        )}

        {workflows?.length === 0 && (
          <p className="px-3 py-2 text-sm leading-relaxed text-muted-foreground">
            No workflows found on this n8n instance.
          </p>
        )}

        {workflows && workflows.length > 0 && (
          <ul className="grid gap-0.5">
            {workflows.map((workflow) => (
              <li key={workflow.id}>
                <WorkflowListItem
                  workflow={workflow}
                  focused={focusedWorkflowId === workflow.id}
                  onSelect={() => selectWorkflow(workflow)}
                />
                {focusedWorkflowId === workflow.id && workflow.failed_count > 0 && (
                  <WorkflowErrorPanel
                    workflowId={workflow.id}
                    failedCount={workflow.failed_count}
                    diagnoseDisabled={busy}
                    onDiagnose={diagnose}
                  />
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-baseline justify-between px-3 pt-5 pb-2">
          <h2 className="chart-label">Chats</h2>
          {threads && <span className="font-mono text-[11px] text-muted-foreground">{threads.length}</span>}
        </div>

        {threads === null && !threadsError && (
          <div aria-label="Loading chats" className="grid gap-2 px-3 py-1">
            <Skeleton className="h-4 w-4/5 bg-secondary" />
            <Skeleton className="h-3 w-full bg-secondary" />
          </div>
        )}
        {threads === null && threadsError && (
          <p role="alert" className="px-3 py-1 text-xs leading-relaxed text-muted-foreground">
            Couldn&apos;t load chats. {threadsError}
          </p>
        )}
        {threads?.length === 0 && (
          <p className="px-3 py-1 text-sm leading-relaxed text-muted-foreground">No chats yet.</p>
        )}
        {threads && threads.length > 0 && (
          <ul aria-label="Chats" className="grid gap-0.5">
            {threads.map((thread) => (
              <li key={thread.id}>
                <ThreadListItem
                  thread={thread}
                  active={activeThread?.id === thread.id}
                  onSelect={() => selectThread(thread.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {workflows && workflowsError && lastSyncedAt && (
        <p role="status" className="border-t px-5 py-2.5 text-xs leading-relaxed text-warn">
          Refresh failed. Showing data from {relativeTime(new Date(lastSyncedAt).toISOString()).toLowerCase()}.
        </p>
      )}

      <UserMenu />
    </div>
  );
}
