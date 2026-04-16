# Elaxtra Autonomous Outreach System

Production-ready event-driven outreach system for [Elaxtra Advisors](https://elaxtra.com). Uses **Anthropic Managed Agents** (beta) + **AgentMail** to run B2B outreach and reply handling without human intervention.

## What it does

1. **Scheduled outreach** — a cron job reads contacts from an Excel file, composes personalized cold emails via a Managed Agent, and sends them through AgentMail.
2. **Real-time reply handling** — when a contact replies, AgentMail fires a webhook, the Responder agent classifies the reply and generates a response, and optionally the Scheduler agent books a discovery call via MS 365.

All emails are signed by Andrew Burgert and BCC'd to his address.

## Architecture

```
┌─────────────────────────┐      ┌──────────────────────────┐
│  APScheduler (9/13/17)  │──┐   │  AgentMail webhook POST  │
└─────────────────────────┘  │   └──────────────┬───────────┘
                             │                  │
                             ▼                  ▼
                      ┌────────────────────────────────┐
                      │  FastAPI app (single process)  │
                      │  ├─ /webhooks/agentmail        │
                      │  └─ Scheduler triggers batch   │
                      └────────────┬───────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
      Composer Agent        Responder Agent       Scheduler Agent
      (Managed Agent)       (Managed Agent)       (Managed Agent)
              │                    │                    │
              └────────────────────┴────────────────────┘
                                   │
                            ┌──────┴──────┐
                            ▼             ▼
                      AgentMail API   Postgres DB
```

## Stack

| Layer | Tech |
|---|---|
| Agent runtime | Anthropic Managed Agents (`managed-agents-2026-04-01`) |
| Email | AgentMail Python SDK + webhooks |
| Webhook server | FastAPI + uvicorn |
| Scheduler | APScheduler (AsyncIOScheduler) |
| DB | Postgres 16 via asyncpg + SQLAlchemy 2.0 + Alembic |
| Config | pydantic-settings |
| Logging | structlog |

## Agents

All agents are created **once** (via `setup` CLI) and their IDs persisted to `.env`. Sessions reference them by ID.

- **Composer** — writes one personalized outreach email given a contact + the company profile PDF
- **Responder** — classifies a reply (INTERESTED / OBJECTION / OPT_OUT / QUESTION / AUTO_REPLY / REFERRAL) and drafts a response
- **Scheduler** — proposes times against Elaxtra's MS 365 calendar and books confirmed calls

Orchestration (Excel reading, batch selection, rate limits) is **plain Python** — not a Managed Agent. No reason to burn tokens on code that doesn't need reasoning.

## Prerequisites

1. Python 3.12+
2. Docker + Docker Compose (for Postgres)
3. Accounts / API keys:
   - Anthropic API key with Managed Agents beta access
   - AgentMail workspace + inbox (elaxtra@agentmail.to)
   - MCP server access (HubSpot, Apollo, Apify, MS 365)
4. Files:
   - `./data/contacts.xlsx` — your contacts list
   - `./docs/elaxtra_company_profile.pdf` — company profile (knowledge base for agents)

## Setup

### 1. Configure environment

```bash
cp .env.example .env
# Fill in API keys, webhook secret, and paths
```

### 2. Start Postgres

```bash
docker compose up -d db
```

### 3. Install dependencies (for local dev) or build the image (for prod)

```bash
# Local
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Or
docker compose build app
```

### 4. Run migrations

```bash
alembic upgrade head
```

### 5. One-time Managed Agents setup

This creates the environment, vault, agents, uploads the company profile, and writes all the IDs back to `.env`:

```bash
python -m src.main setup
```

After this runs, your `.env` will have `COMPOSER_AGENT_ID`, `ENVIRONMENT_ID`, `VAULT_ID`, etc. populated. **Do not run `setup` again** unless you want fresh agents — use `python -m src.main update-agents` instead to version existing ones.

### 6. Seed vault with MCP credentials

Add your MCP server OAuth credentials to the vault (vault ID is in `.env` after setup):

```bash
python -m src.main add-credential --server hubspot --token <oauth-token>
python -m src.main add-credential --server apollo --token <oauth-token>
# ...
```

### 7. Register the AgentMail webhook

```bash
python -m src.main setup-webhook --url https://your-domain.com/webhooks/agentmail
```

### 8. Import contacts

```bash
python -m src.main import-contacts --file ./data/contacts.xlsx
```

### 9. Run

```bash
# Development — runs scheduler + webhook server
python -m src.main serve

# Production via Docker
docker compose up -d
```

## Deploy to Railway

### First-time setup

1. Push your code to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
3. Select your repo and branch `main` — Railway auto-detects `railway.json` and uses the Dockerfile
4. Add a PostgreSQL plugin: Railway dashboard → New → Database → PostgreSQL
5. Link the Postgres service to your app — Railway auto-populates `DATABASE_URL` on the app service
6. Set environment variables in the Railway dashboard (Settings → Variables). Copy from `.env.example`, but:
   - **Do NOT set `PORT`** — Railway injects it automatically
   - **Do NOT manually set `DATABASE_URL`** — the Postgres plugin sets it when linked
7. Grab the generated public domain from the Railway dashboard (e.g., `https://elaxtra-outreach-production.up.railway.app`)

### Auto-deploy

Every push to `main` triggers a redeploy. Railway rebuilds the Docker image and restarts the service. The `startCommand` in `railway.json` runs `alembic upgrade head` before launching the app, so migrations are applied automatically on each deploy.

`watchPatterns` in `railway.json` scope rebuilds to `src/`, `alembic/`, `Dockerfile`, and `pyproject.toml` — other changes (README, tests) ship without a rebuild.

### One-time agent setup (after first deploy)

Managed Agents and the company profile PDF only need to be created once. Run setup from your laptop against the Railway DB, or use the Railway CLI:

```bash
# Option 1: Railway CLI — runs the command inside the service environment
railway run python -m src.main setup
railway run python -m src.main add-credential --server hubspot --url https://mcp.hubspot.com/anthropic --token <access-token>
railway run python -m src.main setup-webhook --url https://your-railway-url.up.railway.app/webhooks/agentmail

# Option 2: SSH-style shell
railway shell
python -m src.main setup
```

Setup writes agent IDs to `.env` locally — copy the new `COMPOSER_AGENT_ID`, `ENVIRONMENT_ID`, `VAULT_ID`, `COMPANY_PROFILE_FILE_ID`, `AGENTMAIL_INBOX_ID` values into Railway's variable settings so the deployed service can reuse them. Redeploy (any push, or hit "Redeploy" in the dashboard) to pick up the new env vars.

### Uploading the contacts file and company profile

Railway's file system is ephemeral — `./data/contacts.xlsx` and `./docs/elaxtra_company_profile.pdf` don't persist across redeploys.

Two ways to handle this:

1. **Persistent volume** (recommended) — add a volume in the Railway dashboard, mount it at `/app/data` and `/app/docs`, and `railway run cp` the files into it.
2. **S3 / external storage** — modify `CONTACTS_EXCEL_PATH` / `COMPANY_PROFILE_PDF_PATH` to point at a mounted path backed by an external blob store.

For the `import-contacts` and initial PDF upload, the simplest flow is to run them locally once against the Railway DB:

```bash
DATABASE_URL='<railway-pg-url>' python -m src.main import-contacts --file ./data/contacts.xlsx
```

### Webhook URL

After deploy, register the AgentMail webhook pointing at the Railway domain:

```bash
railway run python -m src.main setup-webhook --url https://<your-app>.up.railway.app/webhooks/agentmail
```

### Verifying

Once deployed, hit `https://<your-app>.up.railway.app/health` — should return `{"status":"ok"}`. Railway's health check uses the same endpoint.

## CLI

| Command | Purpose |
|---|---|
| `serve` | Start the full system (webhook server + cron scheduler) |
| `setup` | One-time: create environment, vault, agents, upload PDF → write IDs to `.env` |
| `update-agents` | Push new prompt/tool changes as new agent versions |
| `batch` | Run one outreach batch immediately (manual trigger) |
| `import-contacts --file X` | Import contacts from Excel into Postgres |
| `status` | Show rate-limit budget, pending contacts, active sessions |
| `add-credential --server X --token Y` | Store an MCP credential in the vault |
| `setup-webhook --url X` | Register the AgentMail webhook |

`DRY_RUN=true python -m src.main batch` — generate emails but don't send.

## Excel format

Required columns (case-insensitive, any order):

- `Company Name`
- `Contact Name`
- `Contact Email`
- `Contact Role`
- `Company Website`
- `Headcount`
- `Service Type`
- `LinkedIn Person URL`
- `LinkedIn Company URL`
- `Country`
- `Fit` (YES / NO)
- `IT Services` (YES / NO)
- `Outreach Status` (written back by the system)
- `Outreach Date` (written back)

Only rows with `Fit=YES` AND `IT Services=YES` AND empty `Outreach Status` are selected for outreach.

## How reply handling works

1. Contact replies to an outreach email
2. AgentMail delivers the reply and fires `message.received` webhook
3. `POST /webhooks/agentmail` verifies the Svix signature, returns 200 **immediately**, schedules a background task
4. Background task:
   - Deduplicates on `event_id`
   - Looks up the contact by `thread_id`
   - Creates a Managed Agent session for the Responder, passing the PDF + thread + reply
   - Streams the SSE response, parses the JSON output
   - Sends the reply via AgentMail (BCC Andrew), updates DB + Excel
   - If `next_action == SCHEDULE_CALL`, triggers the Scheduler agent

Replies are typically handled within a few seconds of arrival.

## Rate limiting

- Max 25 emails/day (configurable via `MAX_EMAILS_PER_DAY`)
- Max 5 emails/hour
- Min 120s delay between sends
- Tracked in `send_log` table in Postgres

## Testing

```bash
pytest
```

Tests use an in-memory SQLite DB and mock both AgentMail and Anthropic clients.

## Business rules (hard requirements)

1. **BCC andrew.burgert@elaxtra.com on every outgoing email.** No exceptions.
2. **Never email a contact twice** unless they reply first.
3. **OPT_OUT is terminal** — if a contact opts out, mark status `OPTED_OUT` and never contact again.
4. **Emails are signed by Andrew Burgert, Partner, Elaxtra Advisors.**
5. **Webhook must ack in <1s.** All processing is async/background.

## Troubleshooting

- **`cache_read_input_tokens` always 0** — A dynamic value is sneaking into the system prompt. Check `agents/composer.py` and `agents/responder.py` for `datetime.now()` or per-contact interpolation into `system`. Contact data belongs in the user message.
- **Webhook fires but nothing happens** — Check `event_id` dedup table; AgentMail retries on 5xx, but the handler may have logged the error.
- **`setup` failing at vault creation** — Your Anthropic API key may not have Managed Agents beta access. Request access via Console.

## References

- [Managed Agents docs](https://platform.claude.com/docs/en/managed-agents/overview)
- [AgentMail docs](https://docs.agentmail.to)
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
