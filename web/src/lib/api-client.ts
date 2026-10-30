// Browser-side calls to our own /api routes (never the agent or Qloo directly). Every
// failure becomes an ApiError carrying the `{reason}` body the proxy routes emit.
import type {
  BriefOut,
  CandidateOut,
  CompareOut,
  EntityCandidate,
  Reason,
  ResolutionItem,
  Resolutions,
  RunOut,
  VenueHolds,
  VenueId,
} from "@/types/agent";
import type { CrowdProfile } from "@/types/schemas";
import { PLANS, type Plan } from "./entitlements";
import { SAMPLED, busyNote, reasonText, type ErrorDetails, type LimitName } from "./error-states";
import type {
  AnswerInput,
  ConfirmInput,
  PencilInput,
  RefineInput,
  RunInput,
  VenueUpsert,
} from "./schemas";
import { FALLBACK_SEED, isSeedSlug, type SeedSlug } from "./seeds";
import type { PlayedHereMap } from "./played-here";
import type { Precomputed } from "./precomputed";
import type { CrossDomain, SoundcheckData } from "./soundcheck";

export type { BriefOut, CandidateOut, CompareOut, EntityCandidate, Reason, ResolutionItem, Resolutions };
export { reasonText };

export class ApiError extends Error {
  constructor(
    readonly reason: Reason,
    /** Dotted path of the first invalid field, for invalid_input. */
    readonly field?: string,
    /** Resolution keys still to confirm, for conflict on profile. */
    readonly pending?: string[],
    /** The demo venue to show instead, for busy, budget, live_mode_off and plan_limit. */
    readonly fallbackSeed?: SeedSlug,
    /** The plan that ran out of runs today and its allowance, for plan_limit. */
    readonly plan?: Plan,
    readonly runsPerDay?: number,
    /** The Qloo error code and whether a retry can help, for qloo_error. */
    readonly code?: string,
    readonly retryable?: boolean,
    /** The IP window that tripped, its allowance and the seconds to wait, for busy. */
    readonly limit?: LimitName,
    readonly runs?: number,
    readonly retryAfter?: number,
  ) {
    super(reason);
    this.name = "ApiError";
  }
}

export function reasonMessage(err: unknown): string {
  return reasonText(err instanceof ApiError ? err.reason : "unavailable");
}

/** The reason and what came with it, for `<ErrorState>`; a failure that is no ApiError
 * (fetch itself threw) is `unavailable`. */
export function errorOf(err: unknown): { reason: Reason; details: ErrorDetails } {
  if (!(err instanceof ApiError)) return { reason: "unavailable", details: {} };
  const { reason, field, pending, fallbackSeed, plan, runsPerDay, code, retryable } = err;
  const { limit, runs, retryAfter } = err;
  return {
    reason,
    details: { field, pending, fallbackSeed, plan, runsPerDay, code, retryable, limit, runs, retryAfter },
  };
}

/** The seed to show instead of `err` (a refusal of live analysis: the web's rate limit,
 * the daily budget, `LIVE_MODE=off`), or null when `err` is a real failure. */
export function sampleSeed(err: unknown): SeedSlug | null {
  if (!(err instanceof ApiError) || !SAMPLED.has(err.reason)) return null;
  return err.fallbackSeed ?? FALLBACK_SEED;
}

/** Why live analysis was refused, in the booker's words, when the body says more than
 * the reason: the IP window that tripped and the real wait. Null when it says nothing
 * more (a budget or kill-switch refusal, or an older body), and the banner's own text
 * stands. */
export function refusalNote(err: unknown): string | null {
  return err instanceof ApiError ? busyNote(errorOf(err).details) : null;
}

/** A seed's precomputed shortlist, as `GET /api/seeds/{slug}` serves it. */
export interface SeedSample {
  slug: SeedSlug;
  title: string;
  venueId: string;
  venueName: string;
  run: RunOut;
  precomputed: Precomputed;
  crossDomain: CrossDomain;
  played: PlayedHereMap;
}

async function request<T>(method: "GET" | "POST" | "PATCH", path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("unavailable");
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data ?? {}) as {
      reason?: Reason;
      field?: string;
      pending?: string[];
      fallback_seed?: unknown;
      plan?: unknown;
      runs_per_day?: unknown;
      code?: unknown;
      retryable?: unknown;
      limit?: unknown;
      runs?: unknown;
      retry_after?: unknown;
    };
    const seed = isSeedSlug(err.fallback_seed) ? err.fallback_seed : undefined;
    const plan = PLANS.find((p) => p === err.plan);
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
    const code = typeof err.code === "string" ? err.code : undefined;
    const limit = err.limit === "burst" || err.limit === "hourly" ? err.limit : undefined;
    throw new ApiError(
      err.reason ?? "unknown",
      err.field,
      err.pending,
      seed,
      plan,
      num(err.runs_per_day),
      code,
      err.retryable === true,
      limit,
      num(err.runs),
      num(err.retry_after),
    );
  }
  return data as T;
}

const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);
const get = <T>(path: string) => request<T>("GET", path);

const seg = encodeURIComponent;

export const api = {
  /** Free: precomputed rows from the shared cache, never a Qloo or model call. */
  getSeedSample: (slug: SeedSlug) => get<SeedSample>(`/api/seeds/${seg(slug)}`),
  getSoundcheck: (runId: string, entityId: string) =>
    get<SoundcheckData>(`/api/runs/${seg(runId)}/candidates/${seg(entityId)}/soundcheck`),
  getBrief: (runId: string, entityId: string) =>
    get<BriefOut>(`/api/runs/${seg(runId)}/candidates/${seg(entityId)}/brief`),
  setPencil: (runId: string, entityId: string, body: PencilInput) =>
    request<CandidateOut>(
      "PATCH",
      `/api/runs/${seg(runId)}/candidates/${seg(entityId)}`,
      body,
    ),
  getCompare: (runId: string) => get<CompareOut>(`/api/runs/${seg(runId)}/compare`),
  getHolds: (venueId: string) => get<VenueHolds>(`/api/venues/${seg(venueId)}/holds`),
  upsertVenue: (body: VenueUpsert) => post<VenueId>("/api/venues", body),
  resolveVenue: (id: string) => post<Resolutions>(`/api/venues/${seg(id)}/resolve`),
  confirmMatch: (id: string, body: ConfirmInput) =>
    post<ResolutionItem>(`/api/venues/${seg(id)}/confirm`, body),
  buildProfile: (id: string) => post<CrowdProfile>(`/api/venues/${seg(id)}/profile`),
  createRun: (body: RunInput) => post<RunOut>("/api/runs", body),
  refineRun: (runId: string, body: RefineInput) =>
    post<RunOut>(`/api/runs/${seg(runId)}/refine`, body),
  /** The chosen Qloo tag for a word an ask run stopped at: a new run chained to it. */
  answerRun: (runId: string, body: AnswerInput) =>
    post<RunOut>(`/api/runs/${seg(runId)}/answer`, body),
};
