# Outdraw agent for Railway: Python app plus Node 22.19+ and the pinned Qloo harness.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Node 22 from NodeSource; the harness needs >= 22.19.
ARG NODE_MAJOR=22
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22||(a===22&&b<19)) { console.error("Node >= 22.19 required, got "+process.version); process.exit(1) }'

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv

WORKDIR /app

# Pinned harness from package-lock.json; exposes `qloo` via node_modules/.bin.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY draw ./draw
COPY alembic.ini ./
COPY migrations ./migrations
COPY --chmod=755 docker/start.sh ./start.sh

ENV PATH="/app/node_modules/.bin:/app/.venv/bin:${PATH}"

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
# No secrets at build time: QLOO_API_KEY and the rest are Railway env vars read at run time.
# start.sh checks harness readiness, runs migrations, then execs uvicorn on Railway's PORT.
CMD ["./start.sh"]
