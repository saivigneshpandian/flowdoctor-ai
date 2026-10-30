"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useAuth } from "@/components/auth-provider";
import { api, ApiError, errorMessage } from "@/lib/api";
import { parseDecision } from "@/lib/intent";
import type {
  AgentMessage,
  InstructionsMessage,
  ProposalMessage,
  Thread,
  ThreadDetail,
  Workflow,
} from "@/lib/types";

const POLL_INTERVAL_MS = 15_000;
// The agent's reply to a typed rejection (backend/app/agent/graph.py). It has no other signal.
const REJECTED_REPLY = "Fix rejected. Nothing was changed.";

export type SendingKind = "diagnosis" | "decision";

export type ChatEntry =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "agent"; message: AgentMessage };

type ActionStatus =
  | "idle"
  | "approving"
  | "rejecting"
  | "submitting"
  | "approved"
  | "rejected"
  | "failed"
  | "done"
  | "closed";

export interface ActionState {
  status: ActionStatus;
  error?: string;
}

interface WorkspaceValue {
  workflows: Workflow[] | null;
  workflowsError: string | null;
  lastSyncedAt: number | null;
  refreshWorkflows: () => Promise<void>;
  threads: Thread[] | null;
  threadsError: string | null;
  activeThread: Thread | null;
  threadLoading: boolean;
  threadError: string | null;
  focusedWorkflowId: string | null;
  openWorkflow: (workflow: Workflow) => Promise<void>;
  openThread: (threadId: string) => Promise<void>;
  startNewChat: () => void;
  entries: ChatEntry[];
  sending: SendingKind | null;
  busy: boolean;
  actions: Record<string, ActionState>;
  sendMessage: (text: string) => Promise<boolean>;
  decide: (proposalId: string, decision: "approve" | "reject") => Promise<void>;
  completeManualFix: (proposalId: string) => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}

// 404 and 409 mean the proposal is finished or gone on the backend, so retrying can't help.
function isClosedProposal(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 409);
}

// Turns a saved proposal status into what its card shows after a reload.
function savedAction(status: string, isProposal: boolean): ActionState | null {
  switch (status) {
    case "open":
      return null;
    case "applying":
      return { status: isProposal ? "approving" : "submitting" };
    case "approved":
    case "rejected":
    case "failed":
      return { status: isProposal ? status : "closed" };
    case "completed":
      return { status: "done" };
    default:
      return { status: "closed" };
  }
}

function toEntries(detail: ThreadDetail): ChatEntry[] {
  return detail.messages.flatMap((row): ChatEntry[] => {
    const id = `saved-${row.id}`;
    if (row.role === "user") return [{ id, role: "user", content: row.content ?? "" }];
    return row.message ? [{ id, role: "agent", message: row.message }] : [];
  });
}

function readStoredThread(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeThread(key: string, threadId: string | null) {
  try {
    if (threadId) sessionStorage.setItem(key, threadId);
    else sessionStorage.removeItem(key);
  } catch {}
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { state: auth } = useAuth();
  const email = auth.status === "authenticated" ? auth.user.email : "";
  // Per tab and per user, so a refresh reopens the same chat and nobody else's.
  const storageKey = `flowdoctor-thread:${email}`;

  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [focusedWorkflowId, setFocusedWorkflowId] = useState<string | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sendingState, setSendingState] = useState<{ kind: SendingKind; view: number } | null>(null);
  const [viewId, setViewId] = useState(0);
  const [actions, setActions] = useState<Record<string, ActionState>>({});

  const nextId = useRef(0);
  const inFlight = useRef(new Set<string>());
  const pollRunning = useRef(false);
  const pollQueued = useRef(false);
  const chatInFlight = useRef(false);
  // Goes up each time the user switches chats. A reply that was started under an older view
  // belongs to a chat that's no longer on screen, so it must not be added to this one.
  const view = useRef(0);
  const activeThreadId = useRef<string | null>(null);

  const nextView = useCallback(() => {
    view.current += 1;
    setViewId(view.current);
    return view.current;
  }, []);

  const appendAgent = useCallback((messages: AgentMessage[]) => {
    setEntries((current) => [
      ...current,
      ...messages.map((message): ChatEntry => ({
        id: `entry-${nextId.current++}`,
        role: "agent",
        message,
      })),
    ]);
  }, []);

  const refreshWorkflows = useCallback(async () => {
    // A poll already in flight may predate a repair, so a request during it runs again afterwards.
    if (pollRunning.current) {
      pollQueued.current = true;
      return;
    }
    pollRunning.current = true;
    try {
      do {
        pollQueued.current = false;
        try {
          const next = await api.getWorkflows();
          setWorkflows(next);
          setWorkflowsError(null);
          setLastSyncedAt(Date.now());
        } catch (error) {
          setWorkflowsError(errorMessage(error));
        }
      } while (pollQueued.current);
    } finally {
      pollRunning.current = false;
    }
  }, []);

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await api.listThreads());
      setThreadsError(null);
    } catch (error) {
      setThreadsError(errorMessage(error));
    }
  }, []);

  const showThread = useCallback(
    (detail: ThreadDetail) => {
      activeThreadId.current = detail.thread.id;
      storeThread(storageKey, detail.thread.id);
      setActiveThread(detail.thread);
      setFocusedWorkflowId(detail.thread.workflow_id);
      setEntries(toEntries(detail));
      setActions((current) => {
        const next = { ...current };
        for (const row of detail.messages) {
          const message = row.message;
          if (message?.type !== "proposal" && message?.type !== "instructions") continue;
          if (inFlight.current.has(message.proposal_id)) continue;
          const saved = detail.proposal_states[message.proposal_id];
          const action = saved ? savedAction(saved, message.type === "proposal") : null;
          if (action) next[message.proposal_id] = action;
          else delete next[message.proposal_id];
        }
        return next;
      });
    },
    [storageKey],
  );

  const openThread = useCallback(
    async (threadId: string) => {
      const current = nextView();
      setThreadLoading(true);
      setThreadError(null);
      try {
        const detail = await api.getThread(threadId);
        if (view.current === current) showThread(detail);
      } catch (error) {
        if (view.current !== current) return;
        // A saved chat that no longer exists, so start clean rather than show an error forever.
        if (error instanceof ApiError && error.status === 404) {
          activeThreadId.current = null;
          storeThread(storageKey, null);
          setActiveThread(null);
          setFocusedWorkflowId(null);
          setEntries([]);
        } else {
          setThreadError(errorMessage(error));
        }
      } finally {
        if (view.current === current) setThreadLoading(false);
      }
    },
    [nextView, showThread, storageKey],
  );

  const openWorkflow = useCallback(
    async (workflow: Workflow) => {
      const current = nextView();
      activeThreadId.current = null;
      setActiveThread(null);
      setFocusedWorkflowId(workflow.id);
      setEntries([]);
      setThreadLoading(true);
      setThreadError(null);
      try {
        const thread = await api.createThread({
          workflow_id: workflow.id,
          workflow_name: workflow.name,
        });
        const detail = await api.getThread(thread.id);
        if (view.current === current) showThread(detail);
      } catch (error) {
        if (view.current === current) setThreadError(errorMessage(error));
      } finally {
        if (view.current === current) setThreadLoading(false);
      }
    },
    [nextView, showThread],
  );

  const startNewChat = useCallback(() => {
    nextView();
    activeThreadId.current = null;
    storeThread(storageKey, null);
    setActiveThread(null);
    setFocusedWorkflowId(null);
    setEntries([]);
    setThreadError(null);
    setThreadLoading(false);
  }, [nextView, storageKey]);

  useEffect(() => {
    const initial = setTimeout(() => {
      void refreshWorkflows();
      void refreshThreads();
      const stored = readStoredThread(storageKey);
      if (stored) void openThread(stored);
    }, 0);
    const timer = setInterval(() => {
      void refreshWorkflows();
      // Failures diagnosed in the background add chats, so the list has to keep up.
      void refreshThreads();
    }, POLL_INTERVAL_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [openThread, refreshThreads, refreshWorkflows, storageKey]);

  // Adds a result to the chat it belongs to, which may not be the one on screen any more.
  const deliver = useCallback(
    (startedIn: number, threadId: string | null, messages: AgentMessage[]) => {
      if (view.current === startedIn) appendAgent(messages);
      else if (threadId && activeThreadId.current === threadId) void openThread(threadId);
    },
    [appendAgent, openThread],
  );

  const decide = useCallback(
    async (proposalId: string, decision: "approve" | "reject") => {
      if (inFlight.current.has(proposalId)) return;
      inFlight.current.add(proposalId);
      const startedIn = view.current;
      const threadId = activeThreadId.current;
      setActions((current) => ({
        ...current,
        [proposalId]: { status: decision === "approve" ? "approving" : "rejecting" },
      }));

      try {
        if (decision === "approve") {
          const result = await api.approveFix(proposalId);
          const status = result.repair_status === "success" ? "approved" : "failed";
          setActions((current) => ({ ...current, [proposalId]: { status } }));
          deliver(startedIn, threadId, [{ type: "result", ...result }]);
        } else {
          const { ok } = await api.rejectFix(proposalId);
          if (!ok) throw new Error("The backend did not accept the rejection.");
          setActions((current) => ({ ...current, [proposalId]: { status: "rejected" } }));
        }
        void refreshWorkflows();
        void refreshThreads();
      } catch (error) {
        if (isClosedProposal(error)) {
          setActions((current) => ({
            ...current,
            [proposalId]: { status: "closed", error: errorMessage(error) },
          }));
          void refreshWorkflows();
          void refreshThreads();
          return;
        }
        const prefix =
          decision === "approve"
            ? "Approval not confirmed. Nothing is known to be applied."
            : "Rejection not confirmed.";
        setActions((current) => ({
          ...current,
          [proposalId]: { status: "idle", error: `${prefix} ${errorMessage(error)}` },
        }));
      } finally {
        inFlight.current.delete(proposalId);
      }
    },
    [deliver, refreshThreads, refreshWorkflows],
  );

  const completeManualFix = useCallback(
    async (proposalId: string) => {
      if (inFlight.current.has(proposalId)) return;
      inFlight.current.add(proposalId);
      const startedIn = view.current;
      const threadId = activeThreadId.current;
      setActions((current) => ({ ...current, [proposalId]: { status: "submitting" } }));

      try {
        const result = await api.completeFix(proposalId);
        // A failed rerun leaves the proposal open on the backend, so the user can finish and retry.
        const status = result.repair_status === "success" ? "done" : "idle";
        setActions((current) => ({ ...current, [proposalId]: { status } }));
        deliver(startedIn, threadId, [{ type: "result", ...result }]);
        void refreshWorkflows();
        void refreshThreads();
      } catch (error) {
        setActions((current) => ({
          ...current,
          [proposalId]: {
            status: isClosedProposal(error) ? "closed" : "idle",
            error: errorMessage(error),
          },
        }));
      } finally {
        inFlight.current.delete(proposalId);
      }
    },
    [deliver, refreshThreads, refreshWorkflows],
  );

  // The card a typed "yes" or "done" acted on. The backend only applies a decision to a fix in
  // the current chat, so the newest undecided card on screen is the one.
  const openCard = useMemo((): ProposalMessage | InstructionsMessage | null => {
    for (let index = entries.length - 1; index >= 0; index--) {
      const entry = entries[index];
      if (entry.role !== "agent") continue;
      const { message } = entry;
      if (message.type !== "proposal" && message.type !== "instructions") continue;
      if ((actions[message.proposal_id]?.status ?? "idle") === "idle") return message;
    }
    return null;
  }, [actions, entries]);

  // Keeps the cards truthful after a decision that was typed or dictated instead of clicked.
  const settleCards = useCallback(
    (messages: AgentMessage[]) => {
      setActions((current) => {
        const next = { ...current };
        for (const message of messages) {
          // The backend only re-sends open proposals, so an earlier failed attempt can go again.
          if (
            (message.type === "proposal" || message.type === "instructions") &&
            !inFlight.current.has(message.proposal_id)
          ) {
            delete next[message.proposal_id];
          }
          if (!openCard) continue;
          if (message.type === "result") {
            const succeeded = message.repair_status === "success";
            if (openCard.type === "proposal") {
              next[openCard.proposal_id] = { status: succeeded ? "approved" : "failed" };
            } else if (succeeded) {
              next[openCard.proposal_id] = { status: "done" };
            }
          }
          if (message.type === "text" && message.content === REJECTED_REPLY) {
            next[openCard.proposal_id] = {
              status: openCard.type === "proposal" ? "rejected" : "closed",
            };
          }
        }
        return next;
      });
    },
    [openCard],
  );

  const busy =
    sendingState !== null ||
    threadLoading ||
    Object.values(actions).some((action) =>
      ["approving", "rejecting", "submitting"].includes(action.status),
    );

  const sendMessage = useCallback(
    async (text: string): Promise<boolean> => {
      // The ref updates immediately, so a double click can't get in before `busy` re-renders.
      if (busy || chatInFlight.current) return false;
      chatInFlight.current = true;

      const startedIn = view.current;
      const threadId = activeThreadId.current;

      setEntries((current) => [
        ...current,
        { id: `entry-${nextId.current++}`, role: "user", content: text },
      ]);
      setSendingState({ kind: parseDecision(text) ? "decision" : "diagnosis", view: startedIn });

      try {
        const response = await api.chat({
          message: text,
          ...(threadId ? { thread_id: threadId } : {}),
          ...(!threadId && focusedWorkflowId ? { workflow_id: focusedWorkflowId } : {}),
        });
        const savedIn = response.thread_id ?? null;

        if (view.current === startedIn && savedIn && savedIn !== threadId) {
          // A new chat, or a workflow's thread because the message named that workflow.
          void openThread(savedIn);
        } else if (view.current === startedIn) {
          settleCards(response.messages);
          appendAgent(response.messages);
        } else if (savedIn && activeThreadId.current === savedIn) {
          // The user left and came back while this ran, so what's on screen is out of date.
          void openThread(savedIn);
        }
        void refreshWorkflows();
        void refreshThreads();
        return true;
      } catch (error) {
        if (view.current === startedIn) {
          appendAgent([{ type: "error", message: errorMessage(error) }]);
        }
        return false;
      } finally {
        chatInFlight.current = false;
        setSendingState(null);
      }
    },
    [appendAgent, busy, focusedWorkflowId, openThread, refreshThreads, refreshWorkflows, settleCards],
  );

  const sending = sendingState?.view === viewId ? sendingState.kind : null;

  const value = useMemo(
    (): WorkspaceValue => ({
      workflows,
      workflowsError,
      lastSyncedAt,
      refreshWorkflows,
      threads,
      threadsError,
      activeThread,
      threadLoading,
      threadError,
      focusedWorkflowId,
      openWorkflow,
      openThread,
      startNewChat,
      entries,
      sending,
      busy,
      actions,
      sendMessage,
      decide,
      completeManualFix,
    }),
    [
      workflows,
      workflowsError,
      lastSyncedAt,
      refreshWorkflows,
      threads,
      threadsError,
      activeThread,
      threadLoading,
      threadError,
      focusedWorkflowId,
      openWorkflow,
      openThread,
      startNewChat,
      entries,
      sending,
      busy,
      actions,
      sendMessage,
      decide,
      completeManualFix,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
