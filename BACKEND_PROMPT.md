# Build FlowDoctor AI Backend

Act as a senior Python backend engineer experienced with FastAPI, LangGraph and LLM agents.

Read SPEC.md first for full context.

Build the backend for FlowDoctor AI: an agent that connects to a self-hosted n8n instance via its REST API, diagnoses failed workflow executions, proposes fixes, and applies them ONLY after explicit user approval. The Next.js frontend is built separately and expects the exact API contract below.

A real n8n execution error sample is at execution_error_sample.json in the project root. Move it (do not copy) into backend/tests/fixtures/ and use it as the test fixture.

## Tech
- Python 3.11, FastAPI, Uvicorn
- LangGraph for the diagnosis pipeline
- Google Gemini via langchain-google-genai (model name from env GEMINI_MODEL; default to a current Flash model)
- httpx (async) for n8n API calls
- Pydantic v2 for all schemas
- SQLite via SQLModel or sqlite3 (connection settings, proposals, incidents)
- pytest for tests
- Dockerfile; runs in the same docker-compose as n8n on EC2

## Environment variables (.env.example)
GEMINI_API_KEY, GEMINI_MODEL, N8N_URL (optional default, e.g. http://n8n:5678), N8N_API_KEY (optional default), FRONTEND_ORIGIN (CORS), WEBHOOK_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATABASE_PATH.
Never log or return API keys. Add .env to .gitignore.

## n8n API facts (verified against a real instance)
- Auth header: X-N8N-API-KEY
- GET /api/v1/workflows -> {data: [...], nextCursor}
- GET /api/v1/executions?status=error&workflowId=X&limit=N -> list (no node data)
- GET /api/v1/executions/{id}?includeData=true -> full execution. Key fields:
  - data.resultData.lastNodeExecuted -> failing node name
  - data.resultData.error.message / .description -> error text
  - data.resultData.runData[<node name>][0].error -> node-level error
  - data.resultData.runData[<trigger node>][0].data.main[0][*].json -> input data (e.g. body)
  - workflowData -> full workflow JSON (nodes, connections, settings) at execution time
  - Strip all "stack" fields before sending anything to the LLM
- PUT /api/v1/workflows/{id} -> update. Send ONLY name, nodes, connections, settings. Extra fields (id, active, versionId, etc.) are rejected. If settings is rejected, retry with only {"executionOrder": settings.executionOrder}.
- POST /api/v1/workflows/{id}/activate -> makes the updated version live (required after PUT on this n8n version)
- POST /api/v1/executions/{id}/retry with body {"loadWorkflow": true} -> reruns with the CURRENT saved workflow. Without loadWorkflow it reruns the old broken version.

## API contract (must match frontend exactly)
- POST /connect {n8n_url, api_key, telegram_chat_id?} -> {ok, error?}
  Validate by calling GET /workflows. Store settings server-side in SQLite. Never return the key.
- GET /workflows -> [{id, name, status: "healthy"|"failing"|"pending", last_execution_at, failed_count}]
  failed_count = error executions with no successful retry. status: "pending" if an open proposal exists, else "failing" if failed_count > 0, else "healthy".
- POST /chat {message, workflow_id?} -> {messages: AgentMessage[]}
- POST /fix/{proposal_id}/approve -> RepairResult
- POST /fix/{proposal_id}/reject -> {ok}
- POST /fix/{proposal_id}/done -> RepairResult (user finished manual steps)
- GET /incidents -> Incident[]
- POST /webhook/n8n-error (header X-Webhook-Secret must equal WEBHOOK_SECRET)
- GET /health -> {ok: true}

AgentMessage is a discriminated union on "type":
- "text": {content}
- "diagnosis": {workflow_name, failed_node, root_cause, category: "auto_fixable"|"needs_user", impact, confidence (0-1), recommendation}
- "proposal": {proposal_id, workflow_name, node_name, before (JSON), after (JSON), explanation}
- "instructions": {proposal_id, title, steps: string[]}
- "result": RepairResult
- "error": {message}
RepairResult: {repair_status: "success"|"failed", execution_status, recovered_count, verified, message}
Incident: {id, workflow_name, failed_node, root_cause, category, fix_status: "approved"|"rejected"|"open"|"manual", created_at, resolved_at}

## Agent design (LangGraph, used by POST /chat and the webhook)
1. Router: classify intent (status overview, diagnose a workflow, approve/reject pending proposal, user says done). Resolve which workflow the user means by fuzzy-matching names; ask if ambiguous.
2. Fetch: get the latest failed executions for that workflow with includeData=true.
3. Extract: deterministic Python (not LLM) builds a compact context: failing node name/type, its parameters, error message, sample input data, count of affected executions.
4. Diagnose (LLM, structured output): root_cause, category, impact, confidence, recommendation.
   Category rules: credential/auth errors (401, 403, "credentials", "unauthorized", "expired"), permission errors and external service outages -> needs_user. Code, expression, mapping, field-name, null-handling, rate-limit and timeout errors -> auto_fixable.
5. Propose (LLM, structured output): for auto_fixable, return ONLY the new "parameters" object for the failing node. Prefer backward-compatible fixes (e.g. body.email ?? body.Email) so both old and new data shapes work. For needs_user, return numbered steps.
6. Validate (deterministic): failing node still exists, only that node's parameters changed, node count and connections identical, result is valid JSON. For Code nodes, check the JS parses (run `node --check` on a temp file if node is available, else skip). Retry the LLM once with the validation error if invalid; otherwise return an error message.
7. Save the proposal (status open) with the ORIGINAL workflow JSON as a backup. Return diagnosis + proposal (or instructions) messages.

Approval path (deterministic, no LLM):
- approve: PUT patched workflow -> activate -> retry each affected failed execution with loadWorkflow=true -> poll each new execution until finished (timeout 30s) -> recovered_count = successes -> verified = all succeeded. Mark incident approved. On any failure, restore the backup workflow and report honestly.
- reject: mark proposal and incident rejected; change nothing.
- done: retry affected executions and report as above.
If the user's chat message is an approval ("yes, fix it", "approve", "go ahead") or rejection while a proposal is open, route to the same functions.

## Webhook (24/7 path)
POST /webhook/n8n-error receives n8n Error Trigger payloads (execution.id, workflow.id, workflow.name, error). Run fetch -> diagnose -> propose, store the incident, and send a Telegram message: workflow name, failing node, one-line root cause, "Open FlowDoctor to approve". Telegram reply-to-approve is out of scope.

## Safety rules
- Never modify anything without an approved proposal.
- Only modify the failing node's parameters. Never delete nodes, credentials or connections.
- Always keep the original workflow JSON and support rollback.
- Never fabricate results: report the real execution status from n8n.
- Timeouts and clear error messages on every n8n call.

## Project structure
backend/app/main.py, api/routes.py, n8n/client.py, agent/graph.py, agent/prompts.py, agent/extract.py, agent/validate.py, services/repair.py, db.py, schemas.py, telegram.py; backend/tests/; Dockerfile; requirements.txt; .env.example.

## Tests
- Parsing tests against backend/tests/fixtures/execution_error_sample.json (expect failing node "Normalize Lead", error contains "toLowerCase", input body has "Email").
- Validation tests (rejects changed node count, changed connections, invalid JSON).
- n8n client tests with mocked httpx.

## Build order
1. Config, schemas, n8n client, /health, /connect, /workflows
2. extract.py + tests with the fixture
3. LangGraph diagnose + propose + validate, /chat
4. Repair service: approve/reject/done with rollback
5. /incidents, webhook, Telegram
6. Dockerfile and a docker-compose service named "backend" on port 8000

Keep it focused and demo-ready. Don't add features beyond this spec.
