export type WorkflowStatus = "healthy" | "failing" | "pending";

export interface Workflow {
  id: string;
  name: string;
  status: WorkflowStatus;
  last_execution_at: string | null;
  failed_count: number;
}

export interface WorkflowError {
  execution_id: string;
  failed_node: string;
  error_message: string;
}

export type ErrorCategory = "auto_fixable" | "needs_user";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface RepairResult {
  repair_status: "success" | "failed";
  execution_status: string;
  recovered_count: number;
  verified: boolean;
  message: string;
}

export interface TextMessage {
  type: "text";
  content: string;
}

export interface DiagnosisMessage {
  type: "diagnosis";
  workflow_name: string;
  failed_node: string;
  root_cause: string;
  category: ErrorCategory;
  impact: string;
  confidence: number;
  recommendation: string;
}

export interface ProposalMessage {
  type: "proposal";
  proposal_id: string;
  workflow_name: string;
  node_name: string;
  before: JsonValue;
  after: JsonValue;
  explanation: string;
}

export interface InstructionsMessage {
  type: "instructions";
  proposal_id: string;
  title: string;
  steps: string[];
}

export interface ResultMessage extends RepairResult {
  type: "result";
}

export interface ErrorMessage {
  type: "error";
  message: string;
}

export type AgentMessage =
  | TextMessage
  | DiagnosisMessage
  | ProposalMessage
  | InstructionsMessage
  | ResultMessage
  | ErrorMessage;

export type FixStatus = "approved" | "rejected" | "open" | "manual";

export interface Incident {
  id: string;
  workflow_name: string;
  failed_node: string;
  root_cause: string;
  category: ErrorCategory;
  fix_status: FixStatus;
  created_at: string;
  resolved_at: string | null;
}

export interface Credentials {
  email: string;
  password: string;
}

export interface SignupRequest extends Credentials {
  invite_code: string;
}

export interface Me {
  email: string;
  webhook_url: string;
  has_connection: boolean;
  is_demo: boolean;
}

export interface SimulateFailureResult {
  workflow_id: string;
  workflow_name: string;
  // Unresolved failures before the test lead. One more than this means the failure has landed.
  failed_count: number;
  cooldown_seconds: number;
}

export interface ConnectRequest {
  n8n_url: string;
  api_key: string;
  telegram_chat_id?: string;
}

export interface ConnectResponse {
  ok: boolean;
  error?: string;
}

export interface ChatRequest {
  message: string;
  workflow_id?: string;
  thread_id?: string;
}

export interface ChatResponse {
  messages: AgentMessage[];
  // Where this exchange was saved. Naming a workflow in a general chat moves it to that workflow's thread.
  thread_id?: string | null;
}

export interface Thread {
  id: string;
  title: string;
  workflow_id: string | null;
  workflow_name: string | null;
  updated_at: string;
  pending: boolean;
}

export interface ThreadCreate {
  workflow_id?: string;
  workflow_name?: string;
}

export interface ThreadMessage {
  id: number;
  role: "user" | "agent";
  content: string | null;
  message: AgentMessage | null;
  created_at: string;
}

export interface ThreadDetail {
  thread: Thread;
  messages: ThreadMessage[];
  // Proposal id to backend status, so cards show their real state after a reload.
  proposal_states: Record<string, string>;
}

export interface RejectResponse {
  ok: boolean;
}

export interface ApiClient {
  signup(body: SignupRequest): Promise<Me>;
  login(body: Credentials): Promise<Me>;
  logout(): Promise<{ ok: boolean }>;
  me(): Promise<Me>;
  connect(body: ConnectRequest): Promise<ConnectResponse>;
  getWorkflows(): Promise<Workflow[]>;
  getWorkflowError(workflowId: string): Promise<WorkflowError>;
  listThreads(): Promise<Thread[]>;
  createThread(body: ThreadCreate): Promise<Thread>;
  getThread(threadId: string): Promise<ThreadDetail>;
  chat(body: ChatRequest): Promise<ChatResponse>;
  approveFix(proposalId: string): Promise<RepairResult>;
  rejectFix(proposalId: string): Promise<RejectResponse>;
  completeFix(proposalId: string): Promise<RepairResult>;
  getIncidents(): Promise<Incident[]>;
  simulateFailure(): Promise<SimulateFailureResult>;
}
