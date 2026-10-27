# Outdraw

[![ci](https://github.com/ivanmolto/outdraw/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ivanmolto/outdraw/actions/workflows/ci.yml)

Outdraw ([outdraw.live](https://outdraw.live)) is a booking agent for live-music venues and
promoters. Give it a venue, its capacity and a few past shows that sold well, and it works out
who the room's crowd actually is from Qloo's taste graph: the music, bars, films and brands
that audience over-indexes on, tied to the venue's location. It then finds the acts that crowd
will turn out for, ranks them with a transparent score, and writes a booking brief per act
with a suggested night format, a support act and a draft outreach email. The name is booking
jargon: an act's *draw* is how many people it pulls in a market.

Streaming numbers measure global popularity, not whether this room will sell. A plain LLM
asked "who should a jazz bar in NYC book?" invents plausible names. Outdraw's answers come
from Qloo affinity data and every pick opens a Soundcheck panel: the chain from inputs to
Qloo calls to score, each number labelled as Qloo data or Outdraw's interpretation. The model
parses constraints, explains and drafts; it never ranks. Built for the Qloo Agentic Hackathon.

## Architecture

```mermaid
flowchart LR
  B[Browser] --> W["web: Next.js 16 on Vercel<br/>proxies /api/* with X-Draw-Internal-Key"]
  W --> A["agent: FastAPI + Strands on Render<br/>Python 3.12 + Node 22 + pinned harness"]
  A --> G["draw/qloo gateway<br/>envelope normalising, cache, budget, retries"]
  G -->|qloo exec workflow| Q[(Qloo API)]
  G -->|qloo mcp over stdio| Q
  A --> S["draw/ranking/score.py<br/>deterministic score"]
  A --> C[Claude Haiku 5.5]
  A --> P[(Postgres + pgvector)]
  A --> R[(Upstash Redis)]
```

Qloo is reached only through the pinned harness CLI, never raw HTTP: `qloo exec` for scripted
calls and the `qloo mcp` server for the agent's tool calls. `/web` never calls Qloo.

- `web/` — Next.js 16 (App Router, TypeScript, Tailwind v4), deployed on Vercel.
- `agent/` — Python 3.12, FastAPI, Strands Agents SDK, deployed on Render (`render.yaml`).
- `shared/` — JSON Schemas, input limits and the OpenAPI contract; generated into both
  packages by `npm run gen:schemas` and `npm run gen:api` (CI fails on drift).

## Setup from a clean machine

Requirements: Node 22.19+, [uv](https://docs.astral.sh/uv/), Docker (the API tests and the
cache audit start a throwaway Postgres), and a Postgres with pgvector for the agent
(`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=outdraw pgvector/pgvector:pg18` works).

```bash
git clone https://github.com/ivanmolto/outdraw && cd outdraw
npm install && npm --prefix web install
cd agent && npm ci && uv sync && cd ..            # npm ci pins @qloo/qloo-harness 0.1.26
cp .env.example .env.local                        # fill in: see "Bring your own keys"
brew install gitleaks && git config core.hooksPath .githooks   # or your package manager
```

Harness setup (from `agent/`, where `npm ci` put the `qloo` command on `node_modules/.bin`):

```bash
cd agent
npx qloo setup --qloo                             # prompts for the key, stores ~/.qloo/config
npx qloo config set base-url https://hackathon.api.qloo.com
npx qloo setup --status                           # must print "Qloo data: ready"
npx qloo doctor                                   # every Qloo check ok; one "warn model-auth" is expected
```

`qloo setup --status` exits 1 while the harness's optional chat model is unconfigured, so
read the `Qloo data: ready` line, not the exit code. Then migrate, seed and run:

```bash
cd agent && make migrate && cd ..                 # alembic upgrade head, DATABASE_URL from .env.local
set -a && source .env.local && set +a             # the agent does not load .env.local itself
cd agent && uv run python scripts/precompute.py --dry-run && cd ..   # estimates the Qloo calls, spends none
cd agent && uv run python scripts/precompute.py --remaining <quota left> && cd ..   # asks before spending
npm run dev                                       # web on :3000, agent on :8000
```

The seeds are the three demo venues in `agent/seeds/venues.json`; the landing page works
without them but shows no demo cards. Health: `http://localhost:3000/api/health` and
`http://localhost:8000/health` (reports the harness and Node versions).

## Tests

```bash
npm run lint && npm run typecheck && npm test     # eslint, ruff, tsc, mypy strict, vitest, pytest
cd web && npm run test:e2e                        # Playwright against a prod build and mock agent/Redis
cd agent && uv run python scripts/cache_audit.py  # the venue flow twice: the repeat spends nothing
cd agent && RUN_INTEGRATION=1 uv run pytest -m integration   # real keys, spends quota
```

Unit tests run offline against synthetic Qloo envelopes in `agent/tests/fixtures/qloo`
(every status, fake IDs). The API and database tests skip without Docker; CI runs them.

## Deployment

- **Agent on Render**: `render.yaml` is a Blueprint (Docker, `agent/Dockerfile`, health check
  `/health`, deploys only after CI passes). Secrets are `sync: false`, so Render asks for
  them once. The container has no `~/.qloo/config`, so it needs `QLOO_API_KEY`,
  `QLOO_BASE_URL` and `QLOO_TRUSTED_BASE_URL`. `agent/docker/start.sh` checks harness
  readiness, runs migrations, then starts uvicorn.
- **Web on Vercel**: root directory `web`; server-only env vars `AGENT_URL`,
  `INTERNAL_API_KEY`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` and optionally
  `SENTRY_DSN`. Never `NEXT_PUBLIC_`.

## Limits and kill switch

| Limit | Value | Where |
| --- | --- | --- |
| Qloo quota (all harness surfaces, one key) | 10,000 requests/month, 5/s | Qloo |
| Qloo harness spawns per UTC day | `QLOO_DAILY_CAP=300` (cache hits are free) | Redis |
| Model tokens per UTC day | `LLM_DAILY_TOKEN_CAP=2000000` | Redis |
| Per-run call budgets | profile ≤ 6, candidates ≤ 3, signals ≤ 33, briefs ≤ 5 | agent |
| Agent steps per run | 8 tool calls, `max_tokens` on every call | agent |
| Per-IP rate limit on live routes | 3 a minute, 10 an hour (sliding windows) | web, Upstash |

At 70% of a daily cap the agent logs and sends one Telegram alert; at 100% new runs answer
429 `{reason:"budget"}` and the web shows a precomputed seed instead. `GET /limits` shows both
counters. Batch scripts print their call estimate and stop for confirmation, keeping
`QLOO_JUDGING_RESERVE` untouched.

`LIVE_MODE=off` is the kill switch: every route that would spend a Qloo or model call answers
503 `{reason:"live_mode_off", fallback_seed}` before doing any work, while reads, seed pages
and saved venues keep working. Set it in the Render dashboard, not in `render.yaml`.

## Bring your own keys

Outdraw ships without credentials. Every variable is in `.env.example` with a placeholder and
a one-line comment; copy it to `.env.local` (gitignored) and fill in what you have.

- **Qloo**: request a hackathon key on [qloo.devpost.com](https://qloo.devpost.com). Set it
  with `qloo setup --qloo` or export `QLOO_API_KEY`; never the `--api-key` flag, which lands
  in shell history. Hackathon keys only work against `https://hackathon.api.qloo.com`.
- **Anthropic**: `ANTHROPIC_API_KEY`; the model is `ANTHROPIC_MODEL` (Haiku 5.5).
- **Internal**: `INTERNAL_API_KEY` is the shared secret between web and agent
  (`openssl rand -hex 32`, same value on both).
- **Optional**: Upstash Redis (rate limits and daily caps), Sentry, Telegram alerts. Each is
  off when unset; without `DATABASE_URL` the agent uses an in-memory cache.

A gitleaks pre-commit hook and CI job scan the full history with a rule for the Qloo key
format. Qloo response data stays out of the repo: recordings are gitignored.

## Qloo workflows used and why

| Workflow | Use |
| --- | --- |
| `find_tags`, `describe` | Resolve typed acts, venues and genre words to Qloo IDs; ambiguous matches are confirmed by the user before any taste call |
| `recommend` | Candidate acts from the crowd's signals with `signal_location`; the places, films and brands of the crowd profile |
| `compare_audiences` | Genres and past fit against the best-selling past acts; support-act pairing in briefs |
| `where_popular` | Local heat for an act in the venue's region |
| `audience_demographics` | Age skew for the night-format suggestion |
| `trends` | Momentum over the last six months (one request per act) |
| `rank` | Order a shortlist the user already has in mind against the crowd |

The score (`agent/draw/ranking/score.py`) weighs crowd affinity 0.35, past fit 0.20, local
heat 0.20, novelty 0.10, size fit 0.10 and momentum 0.05; a missing Qloo sub-score falls back
to the run's median and is labelled "no Qloo signal". Input shapes that work on the real key
are recorded in `docs/decisions/001-qloo-workflows.md`.

**Walkthrough**: `docs/walkthrough-nyc-jazz.md` lists every Qloo call of a run in order, the
request shape, what the envelope said and how it fed the score, redacted. Regenerate it from
the synthetic fixtures with `cd agent && uv run python scripts/walkthrough.py`.

## Responsible use

Qloo results are aggregate affinities, never claims about individuals. Nothing personal is
sent to Qloo: only entity IDs, tags, regions, dates and the venue's own name and shows. The
UI labels every item as Qloo data or Outdraw's interpretation (`source_labels` in the evidence
trail) and flags partial or degraded envelopes. The key never reaches a prompt or the repo.

## Known limitations

- The harness is new (0.1.26): `summary` is absent on `ok` envelopes, `empty` is never
  returned (`ok` with zero results is treated the same), and `limit` is capped at 20.
- `where_popular` returns heatmap points without names, normalised within one query, so local
  heat is comparable inside a run, not across regions; coverage of small markets is thin.
- Affinity is not ticket sales. Size tiers are popularity terciles of the pool Qloo returns
  for this crowd, not absolute fame.
- The artist side and live Opendate/Gigwell/Prism integrations are not built; the export is
  a CSV or pencil-hold summary.
