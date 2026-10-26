// Typed client for the agent service on Render. Server-only: it reads AGENT_URL and
// INTERNAL_API_KEY from the server environment and sends the key as X-Draw-Internal-Key,
// so the browser reaches the agent only through the /api route handlers.
import "server-only";

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
  VenueId,
  VenueOut,
} from "@/types/agent";
import type { CrowdProfile } from "@/types/schemas";
import { isSeedSlug } from "@/src/lib/seeds";
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
  VenueId,
  VenueOut,
};

// --- errors -----------------------------------------------------------------------------

const PASSTHROUGH: ReadonlySet<string> = new Set<Reason>(["busy", "budget", "live_mode_off"]);

export class AgentError extends Error {
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
  }
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

/** One request to the agent, with the key, a timeout and the error mapping. */
async function transport<T>(
  method: "GET" | "POST" | "PATCH",
  path: string,
  body: unknown,
  timeoutMs: number,
): Promise<T> {
  const { url, key } = config();
  let res: Response;
  try {
    res = await fetch(url + path, {
      method,
      headers: {
        [HEADER]: key,
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    console.error(`agent ${method} ${path} failed:`, err);
    throw new AgentError(503, "unavailable");
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    console.error(`agent ${method} ${path} → ${res.status}`);
    throw toAgentError(res.status, data);
  }
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
): Promise<T> {
  await connection();
  return transport<T>(method, path, body, timeoutMs);
}

const seg = encodeURIComponent;

// --- endpoints --------------------------------------------------------------------------

export const agent = {
  upsertVenue: (body: VenueUpsert) => call<VenueId>("POST", "/venues", body),
  getVenue: (id: string) => call<VenueOut>("GET", `/venues/${seg(id)}`),
  resolveVenue: (id: string) =>
    call<Resolutions>("POST", `/venues/${seg(id)}/resolve`, undefined, SLOW_TIMEOUT_MS),
  confirmMatch: (id: string, body: ConfirmInput) =>
    call<ResolutionItem>("POST", `/venues/${seg(id)}/confirm`, body),
  buildProfile: (id: string) =>
    call<CrowdProfile>("POST", `/venues/${seg(id)}/profile`, undefined, SLOW_TIMEOUT_MS),
  createRun: (body: RunInput) => call<RunOut>("POST", "/runs", body, SLOW_TIMEOUT_MS),
  getRun: (id: string) => call<RunOut>("GET", `/runs/${seg(id)}`),
  /** A new run chained to `id`: one parse call, then a whole agent loop. */
  refineRun: (id: string, body: RefineInput) =>
    call<RunOut>("POST", `/runs/${seg(id)}/refine`, body, SLOW_TIMEOUT_MS),
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
