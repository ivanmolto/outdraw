// Typed client for the agent service on Render. Server-only: it reads AGENT_URL and
// INTERNAL_API_KEY from the server environment and sends the key as X-Draw-Internal-Key,
// so the browser reaches the agent only through the /api route handlers. Every request
// carries X-Draw-Run-Id and logs one line under it (src/server/observability.ts).
import "server-only";

import * as Sentry from "@sentry/nextjs";
import { connection } from "next/server";

import type {
  BriefOut,
  CandidateEvidence,
  CandidateOut,
  CompareOut,
  InvalidInput,
  QlooCall,
  Reason,
  ResolutionItem,
  Resolutions,
  RunOut,
  SeedPageOut,
  SeedsOut,
  SessionOut,
  VenueId,
  VenueHolds,
  VenueOut,
} from "@/types/agent";
import type { CrowdProfile } from "@/types/schemas";
import { isSeedSlug } from "@/src/lib/seeds";
import { RUN_ID_HEADER, logLine, newRunId, runIdFor } from "./observability";
import type {
  ConfirmInput,
  PencilInput,
  RefineInput,
  RunInput,
  VenueUpsert,
} from "@/src/lib/schemas";

// Response shapes live in types/agent.ts, shared with the browser client.
export type {
  BriefOut,
  CandidateEvidence,
  CandidateOut,
  CompareOut,
  QlooCall,
  Reason,
  ResolutionItem,
  Resolutions,
  RunOut,
  SeedPageOut,
  SeedsOut,
  SessionOut,
  VenueId,
  VenueOut,
};

// --- errors -----------------------------------------------------------------------------

const PASSTHROUGH: ReadonlySet<string> = new Set<Reason>(["busy", "budget", "live_mode_off"]);

const REASONS: ReadonlySet<string> = new Set<Reason>([
  "invalid_input",
  "not_found",
  "conflict",
  "budget",
  "busy",
  "live_mode_off",
  "unavailable",
  "unknown",
]);

/** The digest's tag; `NEXT_HTTP_ERROR_FALLBACK;404` is Next's own in the same shape. */
const DIGEST = "DRAW_AGENT";

export class AgentError extends Error {
  /** The X-Draw-Run-Id of the failed request, for the log line and the Sentry tag. */
  runId?: string;
  /** `DRAW_AGENT;{status};{reason}`: what survives a `use cache` boundary. An error thrown
   * out of a cached function (src/server/seeds.ts) reaches its caller serialised, with the
   * message replaced and only the digest kept, as Next does for its own `notFound()`. */
  readonly digest: string;

  constructor(
    readonly status: number,
    readonly reason: Reason,
    /** Items still to confirm, from a 409 on profile. */
    readonly pending?: string[],
    /** The seed the agent named for a refused run (busy, budget, live_mode_off). */
    readonly fallbackSeed?: string,
    /** Dotted path of the field the agent refused, for invalid_input. */
    readonly field?: string,
  ) {
    super(`agent ${status}: ${reason}`);
    this.name = "AgentError";
    this.digest = `${DIGEST};${status};${reason}`;
  }
}

/** The AgentError `err` is, or the one its digest names (its status and reason; the rest
 * does not cross a cache boundary), or null for any other error. */
export function agentError(err: unknown): AgentError | null {
  if (err instanceof AgentError) return err;
  if (typeof err !== "object" || err === null || !("digest" in err)) return null;
  const { digest } = err as { digest?: unknown };
  if (typeof digest !== "string") return null;
  const [tag, status, reason] = digest.split(";");
  if (tag !== DIGEST || !/^\d{3}$/.test(status ?? "") || !REASONS.has(reason ?? "")) return null;
  return new AgentError(Number(status), reason as Reason);
}

function toAgentError(status: number, body: unknown): AgentError {
  const obj = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  // Only a known slug passes: it ends up in a URL on the page.
  const seed = isSeedSlug(obj.fallback_seed) ? obj.fallback_seed : undefined;
  if (typeof obj.reason === "string" && PASSTHROUGH.has(obj.reason)) {
    return new AgentError(status, obj.reason as Reason, undefined, seed);
  }
  switch (status) {
    case 400:
    case 422: {
      // 400 is InvalidInput; 422 is a route's own refusal (not a candidate, no change).
      const { field } = obj as Partial<InvalidInput>;
      return new AgentError(400, "invalid_input", undefined, undefined, field ?? undefined);
    }
    case 404:
      return new AgentError(404, "not_found");
    case 409: {
      const detail = obj.detail as { pending?: unknown } | undefined;
      const pending = Array.isArray(detail?.pending)
        ? detail.pending.filter((p): p is string => typeof p === "string")
        : undefined;
      return new AgentError(409, "conflict", pending);
    }
    case 429:
      return new AgentError(429, "budget", undefined, seed);
    case 502:
    case 503:
    case 504:
      return new AgentError(503, "unavailable");
    default:
      // 401 (key mismatch) and 5xx are our misconfiguration or bug, not the user's.
      return new AgentError(502, "unknown");
  }
}

// --- transport --------------------------------------------------------------------------

export const HEADER = "X-Draw-Internal-Key";
const TIMEOUT_MS = 30_000;
/** Resolve, profile and runs spend Qloo calls (a run is a whole agent loop); keep below
 * the routes' `maxDuration`. */
export const SLOW_TIMEOUT_MS = 110_000;

function config(): { url: string; key: string } {
  const url = process.env.AGENT_URL;
  const key = process.env.INTERNAL_API_KEY;
  if (!url || !key) {
    console.error("AGENT_URL and INTERNAL_API_KEY must be set on the web server.");
    throw new AgentError(503, "unavailable");
  }
  return { url: url.replace(/\/+$/, ""), key };
}

function withRunId(err: AgentError, runId: string): AgentError {
  err.runId = runId;
  return err;
}

/** One request to the agent, with the key, the run id, a timeout and the error mapping. */
async function transport<T>(
  method: "GET" | "POST" | "PATCH",
  path: string,
  body: unknown,
  timeoutMs: number,
  runId: string = runIdFor(path),
): Promise<T> {
  let res: Response;
  const started = Date.now();
  // On the request's isolation scope, so an error thrown past us (a page rendering, not
  // only an /api route through proxy()) still reaches Sentry tagged with the run.
  Sentry.getIsolationScope().setTag("run_id", runId);
  try {
    const { url, key } = config();
    res = await fetch(url + path, {
      method,
      headers: {
        [HEADER]: key,
        [RUN_ID_HEADER]: runId,
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    if (err instanceof AgentError) throw withRunId(err, runId);
    console.error(logLine(`agent ${method} ${path} failed`, runId), err);
    throw withRunId(new AgentError(503, "unavailable"), runId);
  }
  // The agent names a new run itself when our id was taken; log the one it used.
  const used = res.headers.get(RUN_ID_HEADER) ?? runId;
  if (used !== runId) Sentry.getIsolationScope().setTag("run_id", used);
  const line = logLine(`agent ${method} ${path} → ${res.status} ${Date.now() - started}ms`, used);
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    console.error(line);
    throw withRunId(toAgentError(res.status, data), used);
  }
  console.info(line);
  return data as T;
}

/** A live call: agent data is per request. With cacheComponents, Next prerenders a static
 * shell of each page; `connection()` stops that prerender here, before a fetch it would
 * abort (and we'd log as an agent failure). */
async function call<T>(
  method: "GET" | "POST" | "PATCH",
  path: string,
  body?: unknown,
  timeoutMs = TIMEOUT_MS,
  runId?: string,
): Promise<T> {
  await connection();
  return transport<T>(method, path, body, timeoutMs, runId);
}

const seg = encodeURIComponent;

// --- endpoints --------------------------------------------------------------------------

export const agent = {
  /** A new anonymous session on the free plan (T46). */
  createSession: () => call<SessionOut>("POST", "/sessions"),
  getSession: (id: string) => call<SessionOut>("GET", `/sessions/${seg(id)}`),
  upsertVenue: (body: VenueUpsert) => call<VenueId>("POST", "/venues", body),
  getVenue: (id: string) => call<VenueOut>("GET", `/venues/${seg(id)}`),
  getHolds: (id: string) => call<VenueHolds>("GET", `/venues/${seg(id)}/holds`),
  resolveVenue: (id: string) =>
    call<Resolutions>("POST", `/venues/${seg(id)}/resolve`, undefined, SLOW_TIMEOUT_MS),
  confirmMatch: (id: string, body: ConfirmInput) =>
    call<ResolutionItem>("POST", `/venues/${seg(id)}/confirm`, body),
  buildProfile: (id: string) =>
    call<CrowdProfile>("POST", `/venues/${seg(id)}/profile`, undefined, SLOW_TIMEOUT_MS),
  /** The new run's id is ours (`X-Draw-Run-Id`), so both services log it from the start. */
  createRun: (body: RunInput) =>
    call<RunOut>("POST", "/runs", body, SLOW_TIMEOUT_MS, newRunId("run")),
  getRun: (id: string) => call<RunOut>("GET", `/runs/${seg(id)}`),
  /** A new run chained to `id`: one parse call, then a whole agent loop. */
  refineRun: (id: string, body: RefineInput) =>
    call<RunOut>("POST", `/runs/${seg(id)}/refine`, body, SLOW_TIMEOUT_MS, newRunId("run")),
  getEvidence: (runId: string, entityId: string) =>
    call<CandidateEvidence>("GET", `/runs/${seg(runId)}/candidates/${seg(entityId)}/evidence`),
  setPencil: (runId: string, entityId: string, body: PencilInput) =>
    call<CandidateOut>("PATCH", `/runs/${seg(runId)}/candidates/${seg(entityId)}`, body),
  /** One Qloo call and one model call the first time per act, then the cached copy. */
  getBrief: (runId: string, entityId: string) =>
    call<BriefOut>(
      "GET",
      `/runs/${seg(runId)}/candidates/${seg(entityId)}/brief`,
      undefined,
      SLOW_TIMEOUT_MS,
    ),
  getCalls: (runId: string) => call<QlooCall[]>("GET", `/runs/${seg(runId)}/calls`),
  /** One tool-less model call the first time per run, then the cached copy. */
  getCompare: (runId: string) =>
    call<CompareOut>("GET", `/runs/${seg(runId)}/compare`, undefined, SLOW_TIMEOUT_MS),
  /** One Sentry test event on the agent under `runId` (T38; not in the OpenAPI contract). */
  sentryTest: (runId: string) =>
    call<{ run_id: string | null; event_id: string | null }>(
      "POST",
      "/ops/sentry-test",
      undefined,
      TIMEOUT_MS,
      runId,
    ),
};

/** A seed read runs inside a `use cache` scope (src/server/seeds.ts), where `connection()`
 * is not allowed: the caller is already request-bound. */
const seedRead = <T>(path: string) => transport<T>("GET", path, undefined, TIMEOUT_MS);

/** Reads of precomputed seed data for the cached seed pages. Both are reads the agent
 * serves free for a seed; a page not precomputed yet is a 409 (`conflict`), never a call
 * spent. */
export const seedAgent = {
  getSeeds: () => seedRead<SeedsOut>("/seeds"),
  getSeedPage: (slug: string) => seedRead<SeedPageOut>(`/seeds/${seg(slug)}/page`),
};
