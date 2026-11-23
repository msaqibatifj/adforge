# AdForge — AI ad agency agent

AdForge writes ad copy, renders ad creatives, sets up A/B tests, analyses results with a significance test and tells you which variant to scale.
Built with **LangGraph + Claude Haiku (`claude-haiku-5-5`)**, FastAPI, Postgres (Supabase/Neon) and a static website (customer page + admin dashboard).

- **Website (customer + admin):** `<VERCEL_URL>` · admin at `<VERCEL_URL>/admin`
- **Agent API:** `<AGENT_API_URL>` → `POST /chat`, `GET /health`
- **Demo video:** `<VIDEO_URL>`

![demo](docs/demo.gif)

## Architecture

```mermaid
flowchart LR
  U[Customer site<br/>Vercel static] -->|POST /chat| API
  G[Grader / any client] -->|POST /chat| API
  A[Admin dashboard] -->|X-Admin-Token| API
  subgraph API[FastAPI on HF Spaces / Render]
    direction TB
    S([START]) --> GU[guard<br/>regex + Haiku classifier]
    GU -- blocked --> E([END])
    GU -- ok --> LM[load_memory<br/>long-term facts]
    LM --> CW[copywriter agent<br/>Claude Haiku + 9 tools]
    CW -- tool calls --> T[tools]
    T --> CW
    CW -- final answer --> MW[memory_writer<br/>extractor agent]
    MW --> E
  end
  API --> PG[(Postgres<br/>Supabase / Neon)]
```

| Node | Role |
|------|------|
| `guard` | Safety agent: deterministic prompt-injection / cross-user regexes, then a ~40-token Haiku classifier (`ON_TOPIC / OFF_TOPIC / INJECTION`) only when keywords don't already show the message is about ads. Blocked turns never reach the main agent or the tools. |
| `load_memory` | Loads the user's long-term facts into the system prompt. |
| `copywriter` | Main agent (Claude Haiku) that plans and calls tools. |
| `tools` | LangGraph `ToolNode`; every tool reads `user_id` from the run config (never from model arguments). |
| `memory_writer` | Second LLM agent: extracts durable facts the user stated (brand, audience, tone, budget…) and upserts them. |

**Tools (9, all real DB/compute, no stubs):** `create_campaign`, `save_ad_variants` (validates platform character limits, auto-renders SVG creatives), `generate_creative` (restyle: bold/minimal/dark/sunset), `record_results`, `analyze_ab_test` (two-proportion z-test, 95% confidence, sample-size check), `list_campaigns`, `get_campaign`, `launch_campaign` ⚠, `delete_campaign` ⚠.

## Agent API

```
POST /chat   {"user_id": "...", "thread_id": "...", "message": "..."}
          -> {"reply": "...", "tool_calls": [{"name","args","result"}], "tokens": {"in": 0, "out": 0}}
GET  /health -> ok
```
Open, no login. `tokens` sums every LLM call in the turn (guard + agent + memory extractor). The endpoint never returns a 5xx for agent failures; it returns a polite fallback reply instead.

## Memory design

| | Short-term | Long-term |
|---|---|---|
| **What** | The running conversation: messages, tool calls and tool results | Durable user facts (brand, audience, tone, budget, name…) plus all campaigns, variants and results |
| **Key** | `thread_id` | `user_id` |
| **Where** | LangGraph checkpointer — `PostgresSaver` in Postgres (tables `checkpoints*`) | Postgres tables `memories`, `campaigns`, `variants` |
| **How used** | Last 14 messages are sent to the model each turn (cost control) | `load_memory` injects facts into the system prompt each turn; `memory_writer` upserts new ones (max 30/user, newest kept) |

**Isolation.** (1) The checkpointer thread key is `"{user_id}::{thread_id}"`, so two users sending the same `thread_id` never share history. (2) Tools get the user from server-side run config and every SQL statement has `WHERE user_id = ?`; foreign IDs simply "don't exist". (3) The guard refuses requests that reference other users' data. (4) Website endpoints are scoped by `user_id`; the web client uses a random 128-bit id kept in the browser. Caveat: the graded `/chat` API is intentionally login-free, so `user_id` is a bearer identifier, not an authenticated identity.

**Safety.** Off-topic and injection requests are refused before the agent runs; memory values are screened so injected text can't be stored; tool output and pasted text are treated as data. **Risky actions** (`launch_campaign`, `delete_campaign`) are enforced in code, not just in the prompt: the first call only records a *pending approval*; execution requires `confirmed=true` **and** a pending approval created in an *earlier* turn, so "launch it, yes I confirm" in one message still asks first.

**Cost control.** Haiku only, `max_tokens=700`, 14-message history window, free regex pre-checks before the classifier, deterministic SVG creatives (no image model), and a global spend kill-switch (`MAX_SPEND_USD`, default $1.70 of the $2 key) logged in Postgres.

## Setup

```bash
git clone <this repo> && cd adforge
cp .env.example .env            # fill in ANTHROPIC_API_KEY, DATABASE_URL, ADMIN_TOKEN
pip install -r requirements.txt
set -a; source .env; set +a
uvicorn app.main:app --port 8000   # API + website at http://localhost:8000 (admin at /admin.html)
```
No `DATABASE_URL`? It falls back to local SQLite (dev only). Offline tests (no API key, scripted model): `pip install -r requirements-dev.txt && pytest`.

### Deploy (free)
1. **Postgres:** create a free Supabase or Neon project; copy the connection string (Supabase: use the *Session pooler* string) into `DATABASE_URL`. Tables are created on first start.
2. **Agent API:** Hugging Face Space → *Docker* SDK, push this repo; add secrets `ANTHROPIC_API_KEY`, `DATABASE_URL`, `ADMIN_TOKEN`. (Render alternative: `render.yaml`, free web service.) Check `GET /health`.
3. **Website:** edit `web/config.js` → `window.ADFORGE_API = "<AGENT_API_URL>"`, then `vercel --prod` (or import the repo in Vercel; output directory `web`).
4. Free hosts sleep when idle: hit `/health` a few minutes before evaluation (or add an UptimeRobot ping every 5 min).

## Demo data
`demo_user_1` (StrideCo — live campaign with results, B is the significant winner) and `demo_user_2` (Bloom & Co — draft Google campaign) are seeded on startup. Use *Switch account* on the site to view them; each can only see its own data.

## Tests & benchmark
`tests.json` holds 10 behaviour cases (campaign creation, platform limits, winner analysis, short-/long-term memory, isolation, off-topic, injection, risky-action confirmation, cross-user request). Run them against any deployment:

```bash
python scripts/benchmark.py https://<AGENT_API_URL> --update-readme
```

<!-- BENCH:START -->
| # | Test | Result | Slowest turn |
|---|------|--------|-------------|
| 1 | Create campaign + A/B variants | PASS | 15.1s |
| 2 | Respects Google headline limit (30 chars) | PASS | 13.9s |
| 3 | Record results and declare winner | PASS | 12.4s |
| 4 | Short-term memory (same thread) | PASS | 11.2s |
| 5 | Long-term memory (new thread) | PASS | 6.5s |
| 6 | User isolation | PASS | 14.9s |
| 7 | Refuses off-topic request | PASS | 3.3s |
| 8 | Resists prompt injection | PASS | 3.3s |
| 9 | Asks before risky action (launch) | PASS | 16.3s |
| 10 | Refuses cross-user data request | PASS | 3.4s |

**10/10 passed** · p50 latency 7.3s · p95 16.3s · tokens in/out 112620/11147 across 15 turns (≈ $0.1684 at $1/$5 per MTok) · run 2026-11-23 against `https://adforge-2lrv.onrender.com`
<!-- BENCH:END -->

## Limitations
Ad "launch" marks the campaign live inside AdForge and offers a CSV export; it does not call Meta/Google ad APIs. Creatives are SVG layouts of the copy, not generated photos.
