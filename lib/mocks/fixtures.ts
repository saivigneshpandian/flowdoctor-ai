import type {
  DiagnosisMessage,
  Incident,
  InstructionsMessage,
  ProposalMessage,
  RepairResult,
  TextMessage,
  Workflow,
} from "@/lib/types";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

export const healthyWorkflow = (): Workflow => ({
  id: "wf_invoice_sync",
  name: "Invoice sync to Xero",
  status: "healthy",
  last_execution_at: ago(12 * MINUTE),
  failed_count: 0,
});

export const failingWorkflow = (): Workflow => ({
  id: "wf_lead_routing",
  name: "Lead routing to HubSpot",
  status: "failing",
  last_execution_at: ago(2 * HOUR),
  failed_count: 7,
});

export const pendingWorkflow = (): Workflow => ({
  id: "wf_slack_digest",
  name: "Daily Slack digest",
  status: "pending",
  last_execution_at: ago(5 * HOUR),
  failed_count: 2,
});

export const idleWorkflow = (): Workflow => ({
  id: "wf_churn_report",
  name: "Weekly churn report",
  status: "healthy",
  last_execution_at: null,
  failed_count: 0,
});

export const introText: TextMessage = {
  type: "text",
  content:
    "I pulled the last 10 executions of Lead routing to HubSpot. All 7 failures stop at the same node.",
};

export const diagnosis: DiagnosisMessage = {
  type: "diagnosis",
  workflow_name: "Lead routing to HubSpot",
  failed_node: "Map lead fields",
  root_cause:
    'HubSpot renamed the incoming field "company_name" to "company". The expression now resolves to undefined, and the Create Contact call rejects the payload.',
  category: "auto_fixable",
  impact: "7 inbound leads have not reached sales since this morning.",
  confidence: 0.94,
  recommendation:
    'Point the mapping at "company", then replay the 7 failed executions.',
};

export const proposal: ProposalMessage = {
  type: "proposal",
  proposal_id: "prop_lead_mapping",
  workflow_name: "Lead routing to HubSpot",
  node_name: "Map lead fields",
  // The same shape the backend sends: a node view with the new parameters.
  before: {
    name: "Map lead fields",
    type: "n8n-nodes-base.code",
    parameters: {
      jsCode:
        "return $input.all().map(item => ({\n  json: {\n    email: item.json.email.toLowerCase(),\n    company: item.json.company_name\n  }\n}));",
    },
  },
  after: {
    name: "Map lead fields",
    type: "n8n-nodes-base.code",
    parameters: {
      jsCode:
        "return $input.all().map(item => {\n  const company = item.json.company ?? item.json.company_name;\n  if (!company) throw new Error('Lead is missing a company');\n  return {\n    json: {\n      email: item.json.email.toLowerCase(),\n      company\n    }\n  };\n});",
    },
  },
  explanation:
    "Reads the renamed company field as well as the old one, and still fails loudly when a lead has neither. No other node changes.",
};

export const needsUserDiagnosis: DiagnosisMessage = {
  type: "diagnosis",
  workflow_name: "Daily Slack digest",
  failed_node: "Post to #ops",
  root_cause:
    "Slack returned missing_scope. The bot token no longer has chat:write on this workspace.",
  category: "needs_user",
  impact: "The ops team has not received the digest for 2 days.",
  confidence: 0.88,
  recommendation:
    "Reinstall the Slack app with chat:write. Only a workspace admin can do this.",
};

export const instructions: InstructionsMessage = {
  type: "instructions",
  proposal_id: "prop_slack_scope",
  title: "Restore the Slack permission",
  steps: [
    "Open api.slack.com/apps and select the FlowDoctor Digest app.",
    "Under OAuth & Permissions, add the chat:write bot scope.",
    "Click Reinstall to Workspace and approve the prompt.",
    "In n8n, reconnect the Slack credential used by Post to #ops.",
  ],
};

export const successResult: RepairResult = {
  repair_status: "success",
  execution_status: "success",
  recovered_count: 7,
  verified: true,
  message: "The fix is live and all 7 failed executions were replayed.",
};

export const failedResult: RepairResult = {
  repair_status: "failed",
  execution_status: "error",
  recovered_count: 0,
  verified: false,
  message: "The verification run still failed at Map lead fields. Nothing was recovered.",
};

export const manualResult: RepairResult = {
  repair_status: "success",
  execution_status: "success",
  recovered_count: 2,
  verified: true,
  message: "The digest posted to #ops on the test run.",
};

export const incidents = (): Incident[] => [
  {
    id: "inc_1042",
    workflow_name: "Lead routing to HubSpot",
    failed_node: "Map lead fields",
    root_cause: 'Renamed field "company_name" resolves to undefined.',
    category: "auto_fixable",
    fix_status: "open",
    created_at: ago(2 * HOUR),
    resolved_at: null,
  },
  {
    id: "inc_1041",
    workflow_name: "Daily Slack digest",
    failed_node: "Post to #ops",
    root_cause: "Slack bot token is missing the chat:write scope.",
    category: "needs_user",
    fix_status: "manual",
    created_at: ago(2 * DAY),
    resolved_at: null,
  },
  {
    id: "inc_1038",
    workflow_name: "Invoice sync to Xero",
    failed_node: "Create invoice",
    root_cause: "Currency code was lowercase after an upstream change.",
    category: "auto_fixable",
    fix_status: "approved",
    created_at: ago(4 * DAY),
    resolved_at: ago(4 * DAY - 25 * MINUTE),
  },
  {
    id: "inc_1031",
    workflow_name: "Weekly churn report",
    failed_node: "Query warehouse",
    root_cause: "Query timed out while the warehouse was being resized.",
    category: "auto_fixable",
    fix_status: "rejected",
    created_at: ago(9 * DAY),
    resolved_at: ago(9 * DAY - 2 * HOUR),
  },
  {
    id: "inc_1027",
    workflow_name: "Invoice sync to Xero",
    failed_node: "Xero auth",
    root_cause: "OAuth refresh token expired after 60 days without use.",
    category: "needs_user",
    fix_status: "manual",
    created_at: ago(14 * DAY),
    resolved_at: ago(14 * DAY - 3 * HOUR),
  },
];
