"use client";

import { FlaskConical, Loader2, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { CardShell } from "@/components/cards/card-shell";
import { DiagnosisCard } from "@/components/cards/diagnosis-card";
import { InstructionCard, type InstructionStatus } from "@/components/cards/instruction-card";
import { ProposalCard, type ProposalStatus } from "@/components/cards/proposal-card";
import { ResultCard } from "@/components/cards/result-card";
import { ChatInput, type ChatInputHandle } from "@/components/chat-input";
import { EcgTrace, type TraceTone } from "@/components/ecg-trace";
import { StatusDot } from "@/components/status-dot";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspace, type ActionState, type SendingKind } from "@/components/workspace-provider";
import type { AgentMessage, Workflow } from "@/lib/types";

function proposalStatus(action?: ActionState): ProposalStatus {
  switch (action?.status) {
    case "approving":
    case "rejecting":
    case "approved":
    case "rejected":
    case "failed":
    case "closed":
      return action.status;
    default:
      return "awaiting";
  }
}

function instructionStatus(action?: ActionState): InstructionStatus {
  switch (action?.status) {
    case "submitting":
    case "done":
    case "closed":
      return action.status;
    default:
      return "waiting";
  }
}

const PROGRESS: Record<SendingKind, { heading: string; steps: string[] }> = {
  diagnosis: {
    heading: "Diagnosing...",
    steps: ["Reading failed executions", "Finding the root cause", "Preparing a fix"],
  },
  decision: {
    heading: "Working on it...",
    steps: ["Checking the open fix", "Waiting for n8n", "Verifying the result"],
  },
};

const STEP_INTERVAL_MS = 4_000;

function ChatProgress({ kind }: { kind: SendingKind }) {
  const { heading, steps } = PROGRESS[kind];
  const [step, setStep] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setStep((current) => current + 1), STEP_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  return (
    <div
      role="status"
      className="flex max-w-md items-center gap-3 rounded-xl border bg-card px-4 py-3"
    >
      <Loader2 aria-hidden className="size-4 shrink-0 animate-spin text-primary" />
      <p className="text-sm">
        <span className="font-medium">{heading}</span>{" "}
        <span className="text-muted-foreground">{steps[step % steps.length]}</span>
      </p>
    </div>
  );
}

function AgentEntry({ message }: { message: AgentMessage }) {
  const { actions, decide, completeManualFix } = useWorkspace();

  switch (message.type) {
    case "text":
      return <p className="max-w-2xl text-[15px] leading-relaxed whitespace-pre-wrap">{message.content}</p>;
    case "diagnosis":
      return <DiagnosisCard message={message} />;
    case "proposal": {
      const action = actions[message.proposal_id];
      return (
        <ProposalCard
          message={message}
          status={proposalStatus(action)}
          error={action?.error}
          onApprove={() => void decide(message.proposal_id, "approve")}
          onReject={() => void decide(message.proposal_id, "reject")}
        />
      );
    }
    case "instructions": {
      const action = actions[message.proposal_id];
      return (
        <InstructionCard
          message={message}
          status={instructionStatus(action)}
          error={action?.error}
          onDone={() => void completeManualFix(message.proposal_id)}
        />
      );
    }
    case "result":
      return <ResultCard result={message} />;
    case "error":
      return (
        <CardShell label="Something went wrong" tone="fail">
          <p className="flex items-start gap-2 text-sm leading-relaxed">
            <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-fail" />
            {message.message}
          </p>
        </CardShell>
      );
    default:
      return (
        <CardShell label="Unsupported message" tone="neutral">
          <p className="text-sm text-muted-foreground">
            The agent sent a message type this version can&apos;t display.
          </p>
        </CardShell>
      );
  }
}

function SimulateFailure() {
  const { busy, simulation, simulationCoolingDown, simulateFailure } = useWorkspace();
  const breaking = simulation.status === "breaking";

  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy || simulationCoolingDown}
        onClick={() => void simulateFailure()}
        className="gap-1.5"
      >
        {breaking ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <FlaskConical aria-hidden className="size-3.5" />
        )}
        Simulate a failure
      </Button>
      <div role="status" className="min-w-0 text-sm">
        {breaking && (
          <span className="text-muted-foreground">
            Breaking Lead Capture... it&apos;ll turn red in a few seconds
          </span>
        )}
        {simulation.status === "broken" && (
          <span className="font-medium">Now ask: why did Lead Capture fail?</span>
        )}
        {simulation.status === "failed" && <span className="text-fail">{simulation.message}</span>}
      </div>
    </div>
  );
}

function fleetTone(workflows: Workflow[] | null): TraceTone {
  if (!workflows || workflows.length === 0) return "idle";
  if (workflows.some((workflow) => workflow.status === "failing")) return "fail";
  if (workflows.some((workflow) => workflow.status === "pending")) return "warn";
  return "ok";
}

function fleetSummary(workflows: Workflow[] | null): string {
  if (!workflows) return "Reading vitals";
  if (workflows.length === 0) return "No workflows";
  const count = (status: Workflow["status"]) =>
    workflows.filter((workflow) => workflow.status === status).length;
  return `${count("failing")} failing · ${count("pending")} pending · ${count("healthy")} healthy`;
}

export default function DashboardPage() {
  const {
    workflows,
    activeThread,
    threadLoading,
    threadError,
    focusedWorkflowId,
    startNewChat,
    entries,
    sending,
    busy,
    sendMessage,
  } = useWorkspace();
  const { state: auth } = useAuth();
  const isDemo = auth.status === "authenticated" && auth.user.is_demo;

  const input = useRef<ChatInputHandle>(null);
  const log = useRef<HTMLDivElement>(null);
  const focused = workflows?.find((workflow) => workflow.id === focusedWorkflowId) ?? null;

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [entries.length, sending]);

  useEffect(() => {
    if (focusedWorkflowId) input.current?.focus();
  }, [focusedWorkflowId]);

  return (
    <div className="flex h-full flex-col">
      <header className="relative border-b bg-panel/80 backdrop-blur-sm">
        <div aria-hidden className="pointer-events-none absolute inset-y-0 right-80 left-44 hidden opacity-60 md:block">
          <EcgTrace tone={fleetTone(workflows)} />
        </div>
        <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-5 py-3.5 md:px-8">
          <h1 className="min-w-0 truncate text-base font-semibold tracking-tight">
            {activeThread?.title ?? focused?.name ?? "New chat"}
          </h1>
          <p className="font-mono text-xs text-muted-foreground">{fleetSummary(workflows)}</p>
        </div>
      </header>

      <div
        ref={log}
        role="log"
        aria-live="polite"
        aria-label="Conversation with the FlowDoctor assistant"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto grid w-full max-w-4xl gap-4 px-5 py-6 md:px-8">
          {threadLoading && entries.length === 0 && (
            <div aria-label="Loading this chat" className="grid gap-3">
              <Skeleton className="ml-auto h-10 w-1/3 rounded-2xl bg-secondary/70" />
              <Skeleton className="h-40 w-full rounded-xl bg-secondary/70" />
            </div>
          )}

          {threadError && (
            <div role="alert" className="rounded-xl border border-fail/30 bg-fail/10 p-4 text-sm">
              <p className="flex items-center gap-2 font-medium text-fail">
                <TriangleAlert aria-hidden className="size-4" />
                Couldn&apos;t load this chat
              </p>
              <p className="mt-1 text-muted-foreground">{threadError}</p>
            </div>
          )}

          {entries.length === 0 && sending === null && !threadLoading && !threadError && (
            <div className="py-[12vh] text-center">
              <p className="text-2xl font-semibold tracking-tight text-balance md:text-3xl">
                {focused ? `What should I check on ${focused.name}?` : "Which workflow is worrying you?"}
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                Ask why something failed. I&apos;ll diagnose it and propose a fix, and nothing changes
                until you approve it.
              </p>
            </div>
          )}

          {entries.map((entry) =>
            entry.role === "user" ? (
              <div key={entry.id} className="entry-in flex justify-end">
                <p className="max-w-[80%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[15px] leading-relaxed break-words whitespace-pre-wrap">
                  {entry.content}
                </p>
              </div>
            ) : (
              <div key={entry.id} className="entry-in min-w-0">
                <AgentEntry message={entry.message} />
              </div>
            ),
          )}

          {sending && <ChatProgress kind={sending} />}
        </div>
      </div>

      <div className="px-5 pt-1 pb-4 md:px-8 md:pb-6">
        <div className="mx-auto w-full max-w-4xl">
          {isDemo && <SimulateFailure />}
          {focused && (
            <div className="mb-2 flex">
              <span className="inline-flex max-w-full items-center gap-2 rounded-full border bg-panel-raised py-1 pr-1 pl-3 text-xs">
                <StatusDot status={focused.status} />
                <span className="truncate">
                  <span className="text-muted-foreground">Chat about </span>
                  {focused.name}
                </span>
                <button
                  type="button"
                  aria-label={`Leave the ${focused.name} chat and start a new one`}
                  onClick={startNewChat}
                  className="rounded-full p-1 text-muted-foreground outline-none hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X aria-hidden className="size-3" />
                </button>
              </span>
            </div>
          )}
          <ChatInput
            ref={input}
            busy={busy}
            placeholder={focused ? `Ask about ${focused.name}…` : "Why did my workflow fail?"}
            onSend={sendMessage}
          />
        </div>
      </div>
    </div>
  );
}
