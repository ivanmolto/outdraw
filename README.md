# Outdraw

[![ci](https://github.com/ivanmolto/outdraw/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ivanmolto/outdraw/actions/workflows/ci.yml)

Outdraw ([outdraw.live](https://outdraw.live)) helps live-music venues book acts their crowd
will turn out for, using Qloo's taste graph. Built for the Qloo Agentic Hackathon.

## Layout

- `web/` — Next.js 16 (App Router, TypeScript, Tailwind v4), deployed on Vercel.
- `agent/` — Python 3.12 FastAPI service with the pinned Qloo harness, deployed on Render.
- `shared/schemas/` — JSON Schemas (draft 2020-12) shared by both; source of truth for
  `web/types/schemas.ts` and `agent/draw/schemas.py`.

## Requirements

Node 22.19+, [uv](https://docs.astral.sh/uv/), Docker (for the agent image).

## Setup

```bash
npm install && npm --prefix web install && npm --prefix agent ci
cd agent && uv sync
```

Copy `.env.example` to `.env.local` and fill in keys (see "Bring your own keys"). Enable the
secret scan hook once per clone: `brew install gitleaks && git config core.hooksPath .githooks`.

## Bring your own keys

Outdraw ships without credentials. Every variable is listed in `.env.example` with a
placeholder and a one-line comment; copy it to `.env.local` (gitignored) and fill in what you
have. Keys are server-side only and never `NEXT_PUBLIC_`.

- **Qloo**: request a hackathon key on [qloo.devpost.com](https://qloo.devpost.com). The
  agent reaches Qloo only through the pinned harness CLI. Set the key with
  `qloo setup --qloo` (stored in `~/.qloo/config`) or export `QLOO_API_KEY`; never pass it
  with `--api-key`, which lands in your shell history. Hackathon keys only work against
  `https://hackathon.api.qloo.com`, so set `QLOO_BASE_URL` and `QLOO_TRUSTED_BASE_URL` to it
  where there is no config file (the Render container). Then `qloo setup --status && qloo doctor`.
- **Anthropic**: an API key from the Claude Console as `ANTHROPIC_API_KEY`; the model is
  `ANTHROPIC_MODEL` (Haiku 5.5 by default).
- **Internal**: `INTERNAL_API_KEY` is the shared secret between the web server and the agent;
  generate it with `openssl rand -hex 32` and set the same value on both.
- **Optional**: Upstash Redis (rate limits and daily budgets), Postgres with pgvector (the
  Qloo cache), Sentry and Telegram alerts. Each is off when its variable is unset.

Secrets never go in code or git. The pre-commit hook and the `secrets` CI job run
[gitleaks](https://github.com/gitleaks/gitleaks) over the history with the default rules plus
a rule for the Qloo key format (`.gitleaks.toml`); a commit that contains a key is rejected.
Qloo response data stays out of the public repo too: recordings are gitignored.

## Run

```bash
npm run dev          # web on :3000, agent on :8000
```

- `http://localhost:3000/api/health` → `{ok, version}`
- `http://localhost:8000/health` → `{ok, version, qloo_harness_version, node_version}`

## Check

```bash
npm run lint && npm run typecheck && npm test
```

## Schemas

Edit `shared/schemas/*.schema.json`, then regenerate both outputs (never hand-edit them).
The generators merge every file's `$defs` into one bundle, so a `$defs` name must be unique
across all schema files (they fail loudly on a collision). Refer across files with
`Other.schema.json#/$defs/Name`.

```bash
npm run gen:schemas      # web/types/schemas.ts + agent/draw/schemas.py
npm run check:schemas    # what CI runs: regenerate and fail on drift
```

## Agent image

```bash
docker build -t outdraw-agent agent
docker run --rm -p 8000:8000 outdraw-agent
```
