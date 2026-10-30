# FlowDoctor AI frontend

Next.js frontend for FlowDoctor AI: n8n workflow monitoring with AI diagnosis and approval-based repair.

## Run

```bash
npm install
cp .env.example .env.local
npm run dev
```

- `BACKEND_URL`: FastAPI backend base URL, used on the server side. The browser calls `/api/*` on this app, which proxies there.
- `NEXT_PUBLIC_USE_MOCKS`: `true` uses `lib/mocks` and shows a MOCK DATA badge. Any other value uses the real API, with no fallback to mocks.

## Mock demo phrases

| Say or type | Result |
| --- | --- |
| "Why did lead routing fail?" | Diagnosis and repair proposal |
| "Yes, fix it" / "No thanks" | Approves or rejects the waiting proposal |
| "What's wrong with the Slack digest?" | Diagnosis and manual instructions |
| "simulate failed repair" | Proposal whose approval returns a failed result |
| "simulate agent error" | Error card from the agent |
| "simulate outage" | Failed request, with the draft restored |

An n8n URL containing `fail` makes the mock connection fail.
