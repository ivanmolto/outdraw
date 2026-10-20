# Outdraw

Outdraw ([outdraw.live](https://outdraw.live)) helps live-music venues book acts their crowd
will turn out for, using Qloo's taste graph. Built for the Qloo Agentic Hackathon.

## Layout

- `web/` — Next.js 16 (App Router, TypeScript, Tailwind v4), deployed on Vercel.
- `agent/` — Python 3.12 FastAPI service with the pinned Qloo harness, deployed on Railway.
- `shared/` — JSON Schemas shared by both (coming in T04).

## Requirements

Node 22.19+, [uv](https://docs.astral.sh/uv/), Docker (for the agent image).

## Setup

```bash
npm install && npm --prefix web install && npm --prefix agent ci
cd agent && uv sync
```

Copy `.env.example` to `.env.local` and fill in keys. Enable the secret scan hook once per
clone: `brew install gitleaks && git config core.hooksPath .githooks`.

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

## Agent image

```bash
docker build -t outdraw-agent agent
docker run --rm -p 8000:8000 outdraw-agent
```
