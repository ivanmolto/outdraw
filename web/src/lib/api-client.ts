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
  VenueId,
} from "@/types/agent";
import type { CrowdProfile } from "@/types/schemas";
import type { ConfirmInput, PencilInput, RefineInput, RunInput, VenueUpsert } from "./schemas";
import { FALLBACK_SEED, isSeedSlug, type SeedSlug } from "./seeds";
import type { Precomputed } from "./precomputed";
import type { CrossDomain, SoundcheckData } from "./soundcheck";

export type { BriefOut, CandidateOut, CompareOut, EntityCandidate, Reason, ResolutionItem, Resolutions };

export class ApiError extends Error {
  constructor(
    readonly reason: Reason,
    /** Dotted path of the first invalid field, for invalid_input. */
    readonly field?: string,
    /** Resolution keys still to confirm, for conflict on profile. */
    readonly pending?: string[],
    /** The demo venue to show instead, for busy, budget and live_mode_off. */
    readonly fallbackSeed?: SeedSlug,
  ) {
    super(reason);
    this.name = "ApiError";
  }
}

const MESSAGES: Record<Reason, string> = {
  invalid_input: "Some details were rejected. Check the highlighted fields.",
  not_found: "That venue no longer exists. Start again.",
  conflict: "Confirm every Qloo match before building the profile.",
  budget: "Outdraw has used today's Qloo budget. Try again tomorrow.",
  busy: "Outdraw is busy with another request. Try again in a moment.",
  live_mode_off: "Live Qloo lookups are switched off right now.",
  unavailable: "Can't reach Outdraw right now. Check your connection and try again.",
  unknown: "Something went wrong on our side. Try again.",
};

/** User-facing text for a `{reason}`; also used by server components on an AgentError. */
export function reasonText(reason: Reason): string {
  return MESSAGES[reason];
}

export function reasonMessage(err: unknown): string {
  return reasonText(err instanceof ApiError ? err.reason : "unavailable");
}

/** Refusals of live analysis (the web's rate limit, the daily budget, `LIVE_MODE=off`): the
 * UI shows a precomputed sample for these instead of an error. */
const SAMPLED: ReadonlySet<Reason> = new Set(["busy", "budget", "live_mode_off"]);

/** The seed to show instead of `err`, or null when `err` is a real failure. */
export function sampleSeed(err: unknown): SeedSlug | null {
  if (!(err instanceof ApiError) || !SAMPLED.has(err.reason)) return null;
  return err.fallbackSeed ?? FALLBACK_SEED;
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
    };
    const seed = isSeedSlug(err.fallback_seed) ? err.fallback_seed : undefined;
    throw new ApiError(err.reason ?? "unknown", err.field, err.pending, seed);
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
  upsertVenue: (body: VenueUpsert) => post<VenueId>("/api/venues", body),
  resolveVenue: (id: string) => post<Resolutions>(`/api/venues/${seg(id)}/resolve`),
  confirmMatch: (id: string, body: ConfirmInput) =>
    post<ResolutionItem>(`/api/venues/${seg(id)}/confirm`, body),
  buildProfile: (id: string) => post<CrowdProfile>(`/api/venues/${seg(id)}/profile`),
  createRun: (body: RunInput) => post<RunOut>("/api/runs", body),
  refineRun: (runId: string, body: RefineInput) =>
    post<RunOut>(`/api/runs/${seg(runId)}/refine`, body),
};
