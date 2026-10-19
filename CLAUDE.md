# Outdraw — project context for coding assistants

Outdraw (outdraw.live) is an agent that helps live-music venues book acts their crowd will
turn out for, using Qloo's taste graph. Submission for the Qloo Agentic Hackathon on Devpost
(deadline Oct 30, 2026). The name is booking jargon: an act's "draw" is how many people it
pulls in a market; to "outdraw" a rival act is to pull a bigger crowd.

Read `docs/briefing.md` for the product and `docs/tasks.md` for the task list before
starting any task. Each task there has a prompt, a definition of done and tests.

## Repo layout (monorepo)

- `/web` — Next.js 16 (App Router, TypeScript, Tailwind), deployed on Vercel.
- `/agent` — Python 3.12, FastAPI, Strands Agents SDK, deployed on Railway in a container
  that also has Node 22.19+ and the pinned Qloo harness.
- `/shared` — JSON Schemas shared by both; generated TypeScript types live in
  `/web/src/types`, generated Pydantic models in `/agent/draw/schemas.py`. Regenerate with
  the scripts in each package; never hand-edit generated files.
- `/docs` — briefing, task list, decisions (`docs/decisions/*.md`), ops, walkthrough.

## Qloo access (the rules that matter most)

- Qloo is reached ONLY through the Qloo harness CLI: `qloo exec <workflow>` for scripted
  calls, and the `qloo mcp` stdio server for the agent via Strands' MCP client. Never raw
  HTTP to Qloo. Never the harness's integrate/plan/build modes or the legacy `qloo api mcp`.
- The harness version is pinned in `/agent/package.json`. Do not bump it without updating
  `docs/decisions/001-qloo-workflows.md`.
- Workflows: `find_tags`, `describe`, `recommend`, `rank`, `where_popular`,
  `compare_audiences`, `audience_demographics`, `trends`. Input shapes that work on the
  real key are recorded in `docs/decisions/001-qloo-workflows.md`; use those.
- Every result is an envelope `{status, summary, results, result_count}` with status one of
  `ok | empty | needs_input | partial | degraded | error`. Code must branch on all six:
  `empty` → refine input; `needs_input` → surface candidates and ask the user;
  `partial`/`degraded` → check `summary` and label the result; `error` → read `error.code`,
  retry only if `error.retryable`, show `error.recovery`.
- Follow resolve → confirm → ask: turn words into IDs with `find_tags`/`describe`, confirm
  the match with the user when ambiguous, and only then call a taste workflow. Never call a
  taste workflow on an unconfirmed entity.
- All Qloo calls run inside `/agent` (serverless is unsupported). `/web` never calls Qloo;
  it proxies to `/agent`.
- `/agent/draw/qloo/` is the only Qloo gateway: it normalises the envelope, caches, bounds
  retries, counts against the daily budget and records redacted request/response pairs.
  No other module may shell out to `qloo` or import an MCP client.
- When spawning `qloo mcp`, pass the full process environment to the child; otherwise the
  server never sees the key and fails with `QLOO_AUTH`.
- All four harness surfaces share one quota. Make the smallest request that answers the
  question, cache, and keep per-run call budgets (profile ≤ 6, signals ≤ 33, briefs ≤ 5).

## Model and ranking

- LLM: Gemini 3.1 Flash-Lite through Strands' Gemini provider (`GEMINI_MODEL` env).
- The LLM orchestrates, parses constraints, explains and drafts. It NEVER ranks. Ranking is
  the deterministic scoring function in `/agent/draw/ranking/score.py`, with weights
  exported for the UI. If a change would let the model influence order, it is wrong.
- Agent limits: max 8 tool-call steps per run, `max_tokens` on every call, daily token
  budget checked before each run.
- Never put the Qloo key, raw envelopes or personal data in a prompt.

## Data and secrets

- Postgres (Neon or Supabase) with pgvector; Redis (Upstash) for rate limits and budgets.
- Secrets never in code or git: `.env.local` (gitignored) locally, Vercel/Railway env vars
  in prod. Keys are server-side only; never `NEXT_PUBLIC_`.
- `QLOO_API_KEY` is set via env or `qloo setup --qloo`, never the `--api-key` flag.
- Never run `qloo explore` in a folder containing secrets.
- Responsible use: never send names, emails, device IDs or location histories to Qloo.
  Results are aggregate affinities, never claims about individuals. The UI labels every
  item as Qloo data or Outdraw's interpretation (`source_labels` in the evidence trail).

## Style and testing

- Small modules, typed interfaces, tests next to code.
- Python: `uv`, pytest, ruff, mypy strict. TypeScript: vitest, eslint, strict mode.
- Unit tests run offline. Qloo and Gemini are mocked with recorded envelopes in
  `/agent/tests/fixtures/qloo/<workflow>/*.json`; each fixture is a full envelope so status
  branches can be tested.
- Integration tests are marked `@pytest.mark.integration` /
  `describe.skipIf(!process.env.RUN_INTEGRATION)` and need real keys.
- CI runs lint, type-check and unit tests on every push; generated schema files must not
  drift.
- Never commit after each green test run just share a commit plan to do by a human with conventional commit title/s, very concise description/s, files to include in each commit, and terminal commands.

## Commands

```
# web
cd web && npm run dev          # http://localhost:3000
cd web && npm test && npm run lint && npm run typecheck

# agent
cd agent && uv run uvicorn draw.main:app --reload
cd agent && uv run pytest && uv run ruff check . && uv run mypy .
cd agent && uv run alembic upgrade head
cd agent && uv run python scripts/precompute.py     # seed venues, QLOO_RECORD=1

# qloo harness (inside /agent, after npm ci)
qloo setup --status && qloo doctor
```

## Brand

Mark and wordmark live in `web/public/brand/` (from `outdraw-production.zip`). Palette:
near-black `#0E0F14`, off-white `#F6F4EE`, amber `#F5B400`. Amber is used only on dark
grounds. No gradients or effects.
