# FlowDoctor AI — Project Spec

Voice-first "on-call doctor" for n8n automations. Built for **No Hands: The Wispr Challenge** (WeMakeDevs x Wispr Flow), Builders track. Solo build.

## 1. Problem

Businesses increasingly run on automation tools like n8n. When a workflow silently fails (often at night), leads, orders and messages are lost, and nobody notices until the damage is done. Debugging means digging through execution logs node by node: slow, technical and stressful.

## 2. Solution

A web app + AI agent that connects to an n8n instance through its REST API and:

1. Shows the health of every workflow (green / red / amber, failed run counts).
2. Lets the user ask, by voice (Wispr Flow dictation), e.g. "Why did my lead capture workflow fail last night?"
3. Reads the failed executions, finds the failing node and root cause, and explains it in plain language.
4. Classifies the failure:
   - **auto_fixable**: wrong field names, broken code/expressions, bad data mapping, missing null checks, rate limits, timeouts.
   - **needs_user**: expired/missing credentials, permission errors, external service down.
5. For auto_fixable: proposes a patch, shows a before/after diff of the node, waits for approval, then updates the workflow via API, republishes it and reruns the failed executions.
6. For needs_user: gives exact numbered steps; when the user says "done", it reruns.
7. 24/7 monitoring: an n8n Error Trigger workflow posts failures to the backend; the agent diagnoses and sends a Telegram alert.

The agent never changes anything without explicit approval.

## 3. Role of Wispr Flow

Wispr Flow is system-level dictation on the user's laptop. It does NOT connect to n8n. The user holds the Wispr key and speaks; text appears in the app's chat box. The agent replies in text. Wispr Flow is also used to dictate prompts to Claude Code, the README and submission text.

## 4. Architecture

1. **User** speaks -> Wispr Flow types into the chat input.
2. **Frontend (Next.js)** sends the message to the backend.
3. **Backend (Python, FastAPI + LangGraph, Gemini)** reasons and calls tools.
4. **n8n REST API** (self-hosted n8n, Docker, AWS EC2) is called by the backend: list workflows, get executions with data, update workflow, activate, retry executions.
5. **Reply** returns to the chat: diagnosis, proposed fix, diff, Approve/Reject.

Background path: workflow fails -> Error Trigger workflow -> POST backend `/webhook/n8n-error` -> diagnose -> Telegram alert -> user approves in dashboard -> patch + rerun.

n8n is the "patient", not the backend. All product logic is in the Python backend and Next.js frontend.

Design principle: the LLM only diagnoses and proposes. Apply, retry and verify are deterministic code, so the live demo is reliable.

## 5. Tech Stack

- Frontend: Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, Lucide icons
- Backend: Python 3.11, FastAPI, LangGraph, Gemini (langchain-google-genai), httpx, Pydantic v2
- Storage: SQLite (connection settings, proposals, incidents)
- Automation target: n8n self-hosted in Docker on AWS EC2 (t3.small, Ubuntu 26.04, ap-south-1), port 5678
- Notifications: Telegram Bot API
- Deployment: docker-compose on EC2 (services: n8n, backend). Backend reaches n8n internally at http://n8n:5678. Frontend on Vercel or EC2; HTTPS via Caddy + sslip.io later.

## 6. Demo Scenario

The "Lead Capture" workflow: Webhook (POST /webhook/lead-capture) -> Code node "Normalize Lead" -> NoOp "Save Lead".

Normalize Lead code:

```js
return $input.all().map(item => ({
  json: {
    email: item.json.body.email.toLowerCase(),
    name: item.json.body.name
  }
}));
```

The bug: the form now sends `Email` (capital E). `body.email` is undefined, so the node throws
`Cannot read properties of undefined (reading 'toLowerCase') [line 1]`.

Expected fix (backward compatible): `(item.json.body.email ?? item.json.body.Email).toLowerCase()`.

User flow:

1. Open app; first time paste n8n URL + API key; app tests the connection.
2. Dashboard: "Lead Capture" is red with N failed runs.
3. User (via Wispr): "Why did my lead capture workflow fail last night?"
4. Agent: failed at Normalize Lead; code reads `email` but the form now sends `Email`; N leads not saved.
5. Proposal card: before/after code diff, Approve / Reject.
6. User says "Yes, fix it" or clicks Approve.
7. Backend updates the workflow, activates it, retries the failed executions with the current workflow.
8. Result: "All N leads recovered." Workflow turns green.

Credential variant: agent gives steps (e.g. reconnect a credential in n8n), user clicks "I've completed this", backend reruns.

## 7. n8n API Facts (verified on the real instance)

- Auth header: `X-N8N-API-KEY`
- `GET /api/v1/workflows` -> `{data: [...], nextCursor}`
- `GET /api/v1/executions?status=error&workflowId=X&limit=N` -> list without node data
- `GET /api/v1/executions/{id}?includeData=true` -> full execution:
  - `data.resultData.lastNodeExecuted` -> failing node name
  - `data.resultData.error.message` / `.description` -> error text
  - `data.resultData.runData[<node>][0].error` -> node-level error
  - `data.resultData.runData[<trigger node>][0].data.main[0][*].json.body` -> input data
  - `workflowData` -> full workflow JSON at execution time
  - Strip all `stack` fields before sending to the LLM
- `PUT /api/v1/workflows/{id}` -> send ONLY `name`, `nodes`, `connections`, `settings` (extra fields are rejected; if settings is rejected, send only `executionOrder`)
- `POST /api/v1/workflows/{id}/activate` -> required after PUT for changes to go live
- `POST /api/v1/executions/{id}/retry` with `{"loadWorkflow": true}` -> reruns with the CURRENT saved workflow (without it, reruns the old broken version)
- Real sample: `execution_error_sample.json` (move to `backend/tests/fixtures/`)

## 8. Backend API Contract

- `POST /connect {n8n_url, api_key, telegram_chat_id?}` -> `{ok, error?}` (validate with GET /workflows; store server-side; never return the key)
- `GET /workflows` -> `[{id, name, status: "healthy"|"failing"|"pending", last_execution_at, failed_count}]`
- `POST /chat {message, workflow_id?}` -> `{messages: AgentMessage[]}`
- `POST /fix/{proposal_id}/approve` -> RepairResult
- `POST /fix/{proposal_id}/reject` -> `{ok}`
- `POST /fix/{proposal_id}/done` -> RepairResult
- `GET /incidents` -> Incident[]
- `POST /webhook/n8n-error` (header `X-Webhook-Secret`)
- `GET /health` -> `{ok: true}`

AgentMessage (discriminated on `type`):
- `text`: {content}
- `diagnosis`: {workflow_name, failed_node, root_cause, category: "auto_fixable"|"needs_user", impact, confidence (0-1), recommendation}
- `proposal`: {proposal_id, workflow_name, node_name, before, after, explanation}
- `instructions`: {proposal_id, title, steps: string[]}
- `result`: RepairResult
- `error`: {message}

RepairResult: {repair_status: "success"|"failed", execution_status, recovered_count, verified, message}

Incident: {id, workflow_name, failed_node, root_cause, category, fix_status: "approved"|"rejected"|"open"|"manual", created_at, resolved_at}

## 9. Agent Design (LangGraph)

1. Router: intent (status, diagnose, approve/reject, done); fuzzy-match workflow name.
2. Fetch: latest failed executions with includeData=true.
3. Extract (deterministic Python): failing node name/type/parameters, error message, sample input, affected execution count.
4. Diagnose (LLM, structured output).
5. Propose (LLM, structured output): new `parameters` for the failing node only; prefer backward-compatible fixes. Or numbered steps for needs_user.
6. Validate (deterministic): node exists, only that node changed, connections and node count unchanged, valid JSON, JS parses for Code nodes. One LLM retry on failure.
7. Save proposal (open) with original workflow backup.

Approve (deterministic): PUT -> activate -> retry each affected execution with loadWorkflow=true -> poll until finished (30s timeout) -> recovered_count, verified. On failure: restore backup, report honestly.

Safety: never modify without approval; only the failing node's parameters; never delete nodes/credentials/connections; always keep a rollback copy; never fake results.

## 10. Frontend Screens

1. Connect: n8n URL, API key (password), optional Telegram chat ID, Test Connection, status.
2. Dashboard: left sidebar (logo, Dashboard/Incidents nav, workflow list with status dot, last run, failed count); main chat with large voice-friendly input, hint "Hold your Wispr key and speak".
3. Cards: Diagnosis, Proposal (JSON/code diff, Approve/Reject), Instructions ("I've completed this"), Result.
4. Incidents page: history with status filters.

Design: dark navy, subtle borders, green healthy, red failing, amber pending. Calm "medical monitor" feel.

Rules: backend URL from `NEXT_PUBLIC_API_URL`; no secrets in frontend; never store the n8n key after submit; `NEXT_PUBLIC_USE_MOCKS` dev mode with visible "MOCK DATA" badge; dictated "yes, fix it" approves a pending proposal; poll /workflows every 15s; accurate loading/error/pending/success states.

## 11. n8n Setup (done)

- n8n running on EC2 via docker-compose (`~/flowdoctor/docker-compose.yml`), API key created.
- Lead Capture workflow imported, published, and failing on `Email` payloads.
- To do once backend exists: Error Handler workflow (Error Trigger -> HTTP POST to backend `/webhook/n8n-error` with `X-Webhook-Secret`), set as Error Workflow in Lead Capture settings; add 2-3 healthy workflows for the dashboard.

## 12. Submission Checklist

- Public GitHub repo; README: problem, who it's for, how Wispr Flow was used, architecture, setup.
- Live link.
- Demo video, max 2 minutes: hook (lost leads = lost money) -> red workflow -> voice question -> diagnosis -> approve -> green -> Telegram alert.
- Wispr Flow usage card.
- Confirm the exact deadline on wemakedevs.org/wispr.
