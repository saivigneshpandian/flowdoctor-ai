# Complete FlowDoctor AI Frontend

Act as a senior frontend engineer and SaaS product designer.

Read SPEC.md first for full context.

A partial Next.js frontend already exists in this project folder (app/, components/, lib/, .env.local), built earlier in mock mode. Review it, keep what is good, and complete it to this spec. The backend in backend/ is finished, tested and running at http://localhost:8000. **The backend is the source of truth**: read backend/app/schemas.py and backend/app/api/routes.py and make the frontend types and API calls match them exactly. If anything in this prompt disagrees with the backend code, follow the backend code and tell me what differed.

## Product
FlowDoctor AI: users connect their n8n instance, see workflow health, ask an AI agent (by voice via Wispr Flow dictation) why a workflow failed, review the proposed fix, and approve it. The agent never changes anything without approval.

## Technology
Next.js (App Router), TypeScript (strict), Tailwind CSS, shadcn/ui, Lucide icons.

## Design
Premium dark interface inspired by observability dashboards and medical monitors: deep navy background, subtle borders, green = healthy, red = failing, amber = pending approval. Clean typography, responsive, calm "on-call doctor" feel. It must look great in a 2-minute demo video.

## Screens
1. **Connect**: n8n URL, API key (password field), optional Telegram chat ID, Test Connection, status (idle / testing / connected / failed with message). On success go to the dashboard. If the backend already has a connection (GET /workflows succeeds), skip straight to the dashboard.
2. **Dashboard**, two panels:
   - Sidebar: FlowDoctor logo, Dashboard / Incidents nav, workflow list (status dot, name, relative last run, failed count). Clicking a workflow focuses the chat on it (send workflow_id).
   - Main: chat with history, large voice-friendly input, hint "Hold your Wispr key and speak", Send (Enter sends, Shift+Enter newline).
3. **Cards** rendered from AgentMessage types:
   - Diagnosis: workflow, failed node, root cause, category badge (Auto-fixable / Needs your action), impact, confidence bar, recommendation.
   - Proposal: before/after code diff with syntax highlighting (the before/after are strings, often JavaScript from a Code node; show a clear line diff), explanation, Approve / Reject. Amber "Awaiting approval" state.
   - Instructions: numbered steps, "I've completed this" button (calls /fix/{id}/done).
   - Result: repair status, execution status, recovered count, verified.
   - Error: friendly red card with the message.
4. **Incidents** page: history table with status badges and filters (All / Fixed / Rejected / Open / Manual), timestamps.

## Behaviour (important for the live demo)
- Approve, Reject and Done buttons: disable immediately on click and while the request is in flight, so a double click can never send twice. After a decision, the card shows the final state (Approved / Rejected) and the buttons stay disabled.
- /chat can take 5–40 seconds (LLM calls with fallback). Show a clear "Diagnosing..." progress state with a short rotating status (e.g. "Reading failed executions", "Finding the root cause", "Preparing a fix"). Do not time out on the client before 90 seconds.
- Typed or dictated approvals ("yes, fix it", "approve", "go ahead") are already handled by the backend's /chat. Just send them as normal chat messages and render whatever comes back.
- After an approve/done result, refresh GET /workflows immediately so the workflow turns green on screen.
- Poll GET /workflows every 15 seconds on the dashboard.
- Accurate loading, empty, error, pending and success states everywhere. Never show a fake success.

## Integration
- Centralised typed API client in lib/api.ts; base URL from NEXT_PUBLIC_API_URL.
- Types in lib/types.ts generated from the backend schemas.
- Handle non-200 responses (e.g. 409 "already approved/rejected") by showing the backend's detail message.
- Make sure backend CORS (FRONTEND_ORIGIN in backend/.env) allows http://localhost:3000.

## Security
- No API keys or secrets in frontend code.
- The n8n API key is entered once on Connect, sent only to POST /connect, and never stored in localStorage, cookies or state afterwards.

## Mock mode
Keep NEXT_PUBLIC_USE_MOCKS for UI work with a visible "MOCK DATA" badge. Real mode must never fall back to mocks. Set .env.local to NEXT_PUBLIC_USE_MOCKS=false and NEXT_PUBLIC_API_URL=http://localhost:8000.

## Verification (do this before reporting back)
1. npm run build passes with no type errors.
2. Start the dev server and check against the real backend: Connect skips to dashboard, Lead Capture appears with the correct status, Incidents loads.
3. Do NOT approve any proposal yourself. Do NOT call /chat more than once (the LLM free tier is rate-limited). Approving edits the real n8n workflow; I will do the end-to-end test myself.
4. Report what you changed, what differed from this prompt, and anything I need to do.
