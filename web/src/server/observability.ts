// Observability (T38): the run id every agent request carries, the log lines that name it,
// and Sentry without PII. The agent side is agent/draw/observability.py.
//
// Every agent request sends `X-Draw-Run-Id`: a fresh `run_…` id for a new run (POST /runs
// and refine, which the agent adopts as the run's id), the run's own id for any other run
// route, a `req_…` id for venue and seed routes. The agent logs every line under it and
// echoes the id it used on the response, so one run reads the same in both services' logs
// and Sentry events.
//
// Sentry is on only with SENTRY_DSN (server-side only, never NEXT_PUBLIC_; errors only).
// `scrubEvent` removes what could carry a venue name or a booker's message: request bodies,
// cookies, query strings, headers beyond the run id and content type (the internal key
// never leaves), user and frame variables; quoted text in messages, exception values and
// breadcrumbs is replaced, and so is any `name`, `message`, `prompt` or similar key.
import type * as Sentry from "@sentry/nextjs";

type ErrorEvent = Sentry.ErrorEvent;
type SentryOptions = Parameters<typeof Sentry.init>[0];

export const RUN_ID_HEADER = "X-Draw-Run-Id";

/** A new id in the agent's shape (`draw.agent.store.new_id`): `run_` or `req_` + 12 hex. */
export function newRunId(kind: "run" | "req"): string {
  return `${kind}_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

/** The run a path is about (`/runs/{id}/…`), else a fresh request id. */
export function runIdFor(path: string): string {
  const match = /^\/runs\/(run_[a-z0-9_-]{4,64})(?:\/|$)/.exec(path);
  return match ? match[1] : newRunId("req");
}

/** One log line ending in `run_id=…`, the agent's format, so a search finds both sides. */
export function logLine(message: string, runId: string): string {
  return `${message} run_id=${runId}`;
}

// --- Sentry -----------------------------------------------------------------------------

/** How often an `unavailable` (the agent down or unreachable) is reported per process. An
 * outage would otherwise send one event per page view against the free tier's quota; one a
 * minute still shows when it started and when it ended. */
export const UNAVAILABLE_REPORT_INTERVAL_MS = 60_000;
let lastUnavailableReport = -Infinity;

/** Whether this `unavailable` should go to Sentry; every other failure always does. */
export function shouldReportUnavailable(now = Date.now()): boolean {
  if (now - lastUnavailableReport < UNAVAILABLE_REPORT_INTERVAL_MS) return false;
  lastUnavailableReport = now;
  return true;
}

/** Tests only. */
export function resetUnavailableReports(): void {
  lastUnavailableReport = -Infinity;
}

export const REDACTED = "[redacted]";
const SENSITIVE_KEYS: ReadonlySet<string> = new Set([
  "name",
  "venue",
  "venue_name",
  "message",
  "messages",
  "prompt",
  "body",
  "data",
  "act",
  "acts",
  "past_shows",
  "query",
  "x-draw-internal-key",
  "authorization",
  "cookie",
  "cookies",
]);
const SAFE_HEADERS: ReadonlySet<string> = new Set([RUN_ID_HEADER.toLowerCase(), "content-type"]);
const QUOTED = /'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g;

/** Quoted substrings replaced: where user input (names, messages) lands in error text. */
export function redactText(text: string): string {
  return text.replace(QUOTED, `'${REDACTED}'`);
}

function scrub(value: unknown, keys: ReadonlySet<string> = SENSITIVE_KEYS): unknown {
  if (typeof value === "string") return redactText(value);
  if (Array.isArray(value)) return value.map((v) => scrub(v, keys));
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [
        k,
        keys.has(k.toLowerCase()) ? REDACTED : scrub(v, keys),
      ]),
    );
  }
  return value;
}

/** Sentry's `beforeSend`: the event without PII (see the top of this file). */
export function scrubEvent(event: ErrorEvent): ErrorEvent {
  delete event.user;
  if (event.request) {
    const headers = Object.fromEntries(
      Object.entries(event.request.headers ?? {}).filter(([k]) =>
        SAFE_HEADERS.has(k.toLowerCase()),
      ),
    );
    event.request = {
      method: event.request.method,
      url: event.request.url?.split("?", 1)[0],
      headers,
    };
  }
  if (event.extra) event.extra = scrub(event.extra) as ErrorEvent["extra"];
  if (event.contexts) {
    // Sentry's own contexts name the runtime and OS (`runtime.name`): keep `name` there.
    const keys = new Set([...SENSITIVE_KEYS].filter((k) => k !== "name"));
    event.contexts = scrub(event.contexts, keys) as ErrorEvent["contexts"];
  }
  if (event.message) event.message = redactText(event.message);
  if (event.logentry) {
    event.logentry = {
      ...(event.logentry.message && { message: redactText(event.logentry.message) }),
    };
  }
  event.breadcrumbs = event.breadcrumbs?.map((crumb) => ({
    type: crumb.type,
    category: crumb.category,
    level: crumb.level,
    timestamp: crumb.timestamp,
    ...(crumb.message && { message: redactText(crumb.message) }),
    ...(crumb.data && { data: scrub(crumb.data) as Record<string, unknown> }),
  }));
  for (const exc of event.exception?.values ?? []) {
    if (exc.value) exc.value = redactText(exc.value);
    for (const frame of exc.stacktrace?.frames ?? []) delete frame.vars;
  }
  return event;
}

/** Init options for the server and edge runtimes, or null without SENTRY_DSN. */
export function sentryOptions(env: NodeJS.ProcessEnv = process.env): SentryOptions | null {
  if (!env.SENTRY_DSN) return null;
  return {
    dsn: env.SENTRY_DSN,
    environment: env.SENTRY_ENVIRONMENT ?? env.VERCEL_ENV ?? "development",
    tracesSampleRate: 0,
    // Collected as little as the SDK allows; scrubEvent removes what is left.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: { allow: [...SAFE_HEADERS] },
      httpBodies: [],
      urlQueryParams: false,
      genAI: { inputs: false, outputs: false },
      stackFrameVariables: false,
    },
    beforeSend: scrubEvent,
  };
}
