import {
  ago,
  diagnosis,
  failedResult,
  failingWorkflow,
  healthyWorkflow,
  idleWorkflow,
  incidents,
  instructions,
  introText,
  manualResult,
  needsUserDiagnosis,
  pendingWorkflow,
  proposal,
  successResult,
} from "@/lib/mocks/fixtures";
import { parseDecision } from "@/lib/intent";
import { ApiError } from "@/lib/api-error";
import type {
  AgentMessage,
  ApiClient,
  Incident,
  Me,
  RepairResult,
  Thread,
  ThreadMessage,
  Workflow,
  WorkflowError,
} from "@/lib/types";

const FAILING_PROPOSAL_PREFIX = "prop_lead_mapping_retry_";
let failingProposals = 0;
const openFixes = new Map<string, string>();
const fixStates = new Map<string, string>();

interface MockThread {
  thread: Thread;
  messages: ThreadMessage[];
}

const mockThreads = new Map<string, MockThread>();
let nextMockId = 0;

function mockThread(workflowId?: string, workflowName?: string): MockThread {
  const existing = [...mockThreads.values()].find(
    (entry) => workflowId && entry.thread.workflow_id === workflowId,
  );
  if (existing) return existing;
  const id = `thr_mock_${nextMockId++}`;
  const created: MockThread = {
    thread: {
      id,
      title: workflowName ?? "New chat",
      workflow_id: workflowId ?? null,
      workflow_name: workflowName ?? null,
      updated_at: ago(0),
      pending: false,
    },
    messages: [],
  };
  mockThreads.set(id, created);
  return created;
}

function saveMock(entry: MockThread, user: string, replies: AgentMessage[]) {
  const row = (fields: Pick<ThreadMessage, "role" | "content" | "message">): ThreadMessage => ({
    id: nextMockId++,
    created_at: ago(0),
    ...fields,
  });
  entry.messages.push(row({ role: "user", content: user, message: null }));
  for (const message of replies) entry.messages.push(row({ role: "agent", content: null, message }));
  if (entry.thread.title === "New chat") entry.thread.title = user.slice(0, 60);
  entry.thread.updated_at = ago(0);
}

function isPending(entry: MockThread): boolean {
  return openFixes.has(entry.thread.id);
}

const WORKFLOW_ERRORS: Record<string, WorkflowError> = {
  wf_lead_routing: {
    execution_id: "1042",
    failed_node: "Map lead fields",
    error_message: "Cannot read properties of undefined (reading 'trim') [line 4]",
  },
  wf_slack_digest: {
    execution_id: "1041",
    failed_node: "Post to Slack",
    error_message: "The token used is expired (401 Unauthorized)",
  },
};

const delay = (ms = 800) => new Promise((resolve) => setTimeout(resolve, ms));

// Repairs change this in-memory state so the sidebar and incidents page react during a demo.
const repaired = new Set<string>();
const rejected = new Set<string>();

function applyRepairs(workflow: Workflow): Workflow {
  if (!repaired.has(workflow.id)) return workflow;
  return { ...workflow, status: "healthy", failed_count: 0, last_execution_at: ago(0) };
}

function applyIncidentChanges(incident: Incident): Incident {
  if (incident.id === "inc_1042" && repaired.has("wf_lead_routing")) {
    return { ...incident, fix_status: "approved", resolved_at: ago(0) };
  }
  if (incident.id === "inc_1042" && rejected.has(proposal.proposal_id)) {
    return { ...incident, fix_status: "rejected", resolved_at: ago(0) };
  }
  if (incident.id === "inc_1041" && repaired.has("wf_slack_digest")) {
    return { ...incident, resolved_at: ago(0) };
  }
  return incident;
}

// Scripted agent. Trigger phrases let every card and failure state be reached by hand.
function scriptChat(message: string, workflowId?: string): AgentMessage[] {
  const text = message.toLowerCase();

  if (text.includes("simulate outage")) {
    throw new Error("Mock outage: the backend could not be reached.");
  }
  if (text.includes("simulate agent error")) {
    return [{ type: "error", message: "Mock agent error: n8n refused the execution lookup." }];
  }
  if (text.includes("simulate failed repair")) {
    return [{ ...proposal, proposal_id: `${FAILING_PROPOSAL_PREFIX}${failingProposals++}` }];
  }
  if (repaired.has("wf_lead_routing") && (workflowId === "wf_lead_routing" || /lead|hubspot/.test(text))) {
    return [{ type: "text", content: "Lead routing to HubSpot is healthy since the repair." }];
  }
  if (rejected.has(proposal.proposal_id) && (workflowId === "wf_lead_routing" || /lead|hubspot/.test(text))) {
    return [{ type: "text", content: "Lead routing to HubSpot is still failing. You rejected the proposed fix, so nothing changed." }];
  }
  if (workflowId === "wf_slack_digest" || /slack|digest|permission|credential/.test(text)) {
    return [needsUserDiagnosis, instructions];
  }
  if (workflowId === "wf_lead_routing" || /lead|hubspot|fail|broke|why/.test(text)) {
    return [introText, diagnosis, proposal];
  }
  if (workflowId) {
    return [{ type: "text", content: "This workflow's recent executions all succeeded. No action is needed." }];
  }
  return [
    {
      type: "text",
      content:
        "Lead routing to HubSpot is failing and Daily Slack digest needs attention. Which one should I look at?",
    },
  ];
}

function closeMockFix(proposalId: string, status: string) {
  fixStates.set(proposalId, status);
  for (const [threadId, open] of openFixes) {
    if (open === proposalId) openFixes.delete(threadId);
  }
}

// Keeps a button-driven result in the proposal's chat, the same way the backend does.
function recordMockResult(proposalId: string, result: RepairResult): RepairResult {
  const owner = [...mockThreads.values()].find((entry) =>
    entry.messages.some(
      (row) =>
        (row.message?.type === "proposal" || row.message?.type === "instructions") &&
        row.message.proposal_id === proposalId,
    ),
  );
  owner?.messages.push({
    id: nextMockId++,
    role: "agent",
    content: null,
    message: { type: "result", ...result },
    created_at: ago(0),
  });
  return result;
}

const MOCK_USER: Me = {
  email: "demo@flowdoctor.test",
  webhook_url: "http://localhost:8000/webhook/n8n-error/mock-token",
  has_connection: true,
  is_demo: true,
};
const SIMULATE_COOLDOWN_MS = 120_000;
const SIMULATED_FAILURE_DELAY_MS = 3_000;
const SIMULATED_WORKFLOWS = new Set(["wf_lead_routing", "wf_invoice_sync"]);
const lastSimulations = new Map<string, number>();
const simulatedFailures = new Map<string, number>();
const LOGGED_OUT_KEY = "flowdoctor-mock-logged-out";

// Survives the full page load that logout does. Falls back to logged in if storage is blocked.
function setMockLoggedOut(loggedOut: boolean) {
  try {
    if (loggedOut) sessionStorage.setItem(LOGGED_OUT_KEY, "true");
    else sessionStorage.removeItem(LOGGED_OUT_KEY);
  } catch {}
}

function isMockLoggedOut(): boolean {
  try {
    return sessionStorage.getItem(LOGGED_OUT_KEY) === "true";
  } catch {
    return false;
  }
}

export const mockApi: ApiClient = {
  async signup({ email, invite_code }) {
    await delay(700);
    if (invite_code === "wrong") throw new ApiError("That invite code isn't valid.", 403);
    setMockLoggedOut(false);
    return { ...MOCK_USER, email };
  },

  async login({ email, password }) {
    await delay(700);
    if (password === "wrong") throw new ApiError("Incorrect email or password.", 401);
    setMockLoggedOut(false);
    return { ...MOCK_USER, email };
  },

  async logout() {
    await delay(200);
    setMockLoggedOut(true);
    return { ok: true };
  },

  async me() {
    await delay(150);
    if (isMockLoggedOut()) throw new ApiError("Please log in.", 401);
    return MOCK_USER;
  },

  async connect({ n8n_url }) {
    await delay(1100);
    if (n8n_url.includes("fail")) {
      return { ok: false, error: "n8n rejected the API key (401 Unauthorized)." };
    }
    return { ok: true };
  },

  async getWorkflows() {
    await delay(350);
    return [failingWorkflow(), pendingWorkflow(), healthyWorkflow(), idleWorkflow()]
      .map(applyRepairs)
      .map((workflow) => {
        const extra = simulatedFailures.get(workflow.id) ?? 0;
        return {
          ...workflow,
          can_simulate: SIMULATED_WORKFLOWS.has(workflow.id),
          ...(extra > 0
            ? { status: "failing" as const, failed_count: workflow.failed_count + extra }
            : {}),
        };
      });
  },

  async getWorkflowError(workflowId) {
    await delay(350);
    const error = WORKFLOW_ERRORS[workflowId];
    if (!error || repaired.has(workflowId)) throw new Error("This workflow has no unresolved failures.");
    return error;
  },

  async listThreads() {
    await delay(150);
    return [...mockThreads.values()]
      .filter((entry) => entry.messages.length > 0)
      .sort((a, b) => b.thread.updated_at.localeCompare(a.thread.updated_at))
      .map((entry) => ({ ...entry.thread, pending: isPending(entry) }));
  },

  async createThread({ workflow_id, workflow_name }) {
    await delay(150);
    return mockThread(workflow_id, workflow_name).thread;
  },

  async getThread(threadId) {
    await delay(150);
    const entry = mockThreads.get(threadId);
    if (!entry) throw new ApiError("Chat not found.", 404);
    return {
      thread: { ...entry.thread, pending: isPending(entry) },
      messages: entry.messages,
      proposal_states: Object.fromEntries(fixStates),
    };
  },

  async chat({ message, workflow_id, thread_id }) {
    await delay(1300);
    const entry = thread_id ? mockThreads.get(thread_id) : mockThread(workflow_id);
    if (!entry) throw new ApiError("Chat not found.", 404);
    const threadId = entry.thread.id;

    // Typed decisions act on the open fix in this chat, the same way the backend's /chat does.
    const decision = parseDecision(message);
    let messages: AgentMessage[];
    if (decision) {
      const open = openFixes.get(threadId);
      if (!open) {
        messages = [{ type: "text", content: "There is no fix waiting for a decision in this chat." }];
      } else if (decision === "reject") {
        await mockApi.rejectFix(open);
        messages = [{ type: "text", content: "Fix rejected. Nothing was changed." }];
      } else {
        const result =
          decision === "approve" ? await mockApi.approveFix(open) : await mockApi.completeFix(open);
        messages = [{ type: "result", ...result }];
      }
    } else {
      messages = scriptChat(message, entry.thread.workflow_id ?? workflow_id);
      for (const reply of messages) {
        if (reply.type === "proposal" || reply.type === "instructions") {
          openFixes.set(threadId, reply.proposal_id);
          fixStates.set(reply.proposal_id, "open");
        }
      }
    }

    saveMock(entry, message, messages);
    return { messages, thread_id: threadId };
  },

  async approveFix(proposalId) {
    await delay(1600);
    closeMockFix(proposalId, proposalId.startsWith(FAILING_PROPOSAL_PREFIX) ? "failed" : "approved");
    if (proposalId.startsWith(FAILING_PROPOSAL_PREFIX)) return recordMockResult(proposalId, failedResult);
    repaired.add("wf_lead_routing");
    return recordMockResult(proposalId, successResult);
  },

  async rejectFix(proposalId) {
    await delay(600);
    rejected.add(proposalId);
    closeMockFix(proposalId, "rejected");
    return { ok: true };
  },

  async completeFix(proposalId) {
    await delay(1400);
    repaired.add("wf_slack_digest");
    closeMockFix(proposalId, "completed");
    return recordMockResult(proposalId, manualResult);
  },

  async getIncidents() {
    await delay(500);
    return incidents().map(applyIncidentChanges);
  },

  async simulateFailure(workflowId) {
    await delay(900);
    const waited = Date.now() - (lastSimulations.get(workflowId) ?? 0);
    if (waited < SIMULATE_COOLDOWN_MS) {
      const seconds = Math.ceil((SIMULATE_COOLDOWN_MS - waited) / 1000);
      throw new ApiError(`This was run recently. Try again in ${seconds} seconds.`, 429);
    }
    lastSimulations.set(workflowId, Date.now());
    const workflow = (await mockApi.getWorkflows()).find((item) => item.id === workflowId);
    if (!workflow) throw new ApiError("Workflow not found.", 404);
    setTimeout(() => {
      simulatedFailures.set(workflowId, (simulatedFailures.get(workflowId) ?? 0) + 1);
    }, SIMULATED_FAILURE_DELAY_MS);
    return {
      workflow_id: workflow.id,
      workflow_name: workflow.name,
      failed_count: workflow.failed_count,
      cooldown_seconds: SIMULATE_COOLDOWN_MS / 1000,
    };
  },
};
