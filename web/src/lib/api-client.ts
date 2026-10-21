// Browser-side calls to our own /api routes (never the agent or Qloo directly). Every
// failure becomes an ApiError carrying the `{reason}` body the proxy routes emit.
import type {
  EntityCandidate,
  Reason,
  ResolutionItem,
  Resolutions,
  VenueId,
} from "@/types/agent";
import type { CrowdProfile } from "@/types/schemas";
import type { ConfirmInput, VenueUpsert } from "./schemas";

export type { EntityCandidate, Reason, ResolutionItem, Resolutions };

export class ApiError extends Error {
  constructor(
    readonly reason: Reason,
    /** Dotted path of the first invalid field, for invalid_input. */
    readonly field?: string,
    /** Resolution keys still to confirm, for conflict on profile. */
    readonly pending?: string[],
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

export function reasonMessage(err: unknown): string {
  return MESSAGES[err instanceof ApiError ? err.reason : "unavailable"];
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("unavailable");
  }
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data ?? {}) as { reason?: Reason; field?: string; pending?: string[] };
    throw new ApiError(err.reason ?? "unknown", err.field, err.pending);
  }
  return data as T;
}

const seg = encodeURIComponent;

export const api = {
  upsertVenue: (body: VenueUpsert) => post<VenueId>("/api/venues", body),
  resolveVenue: (id: string) => post<Resolutions>(`/api/venues/${seg(id)}/resolve`),
  confirmMatch: (id: string, body: ConfirmInput) =>
    post<ResolutionItem>(`/api/venues/${seg(id)}/confirm`, body),
  buildProfile: (id: string) => post<CrowdProfile>(`/api/venues/${seg(id)}/profile`),
};
