// Every `{reason}` the /api routes emit (src/server/proxy.ts, from AgentError in
// src/server/agent.ts), mapped to what the booker sees: a title, a message, whether a retry
// can help and where to go next. The one table for the whole UI, so a reason reads the same
// on a page, in a panel and under a button. Nothing here comes from the backend: never a
// message, URL or stack of its own.
import type { Reason } from "@/types/agent";
import { FALLBACK_SEED, type SeedSlug } from "./seeds";

export type { Reason };

/** Where the booker can go instead. */
export interface NextLink {
  label: string;
  href: string;
}

export interface ErrorState {
  reason: Reason;
  title: string;
  message: string;
  /** The same request a moment later can succeed. */
  retryable: boolean;
  /** What to do instead of (or as well as) retrying. */
  link: NextLink | null;
  /** For `conflict`: the Qloo matches still to confirm (resolution keys). */
  pending: string[];
}

/** What came with the `{reason}` body, and where it happened. */
export interface ErrorDetails {
  /** The venue the page belongs to: its crowd profile is the way back. */
  venueId?: string;
  /** From a 409 on profile: matches still to confirm. */
  pending?: string[];
  /** The demo venue the agent named instead, for busy, budget and live_mode_off. */
  fallbackSeed?: SeedSlug;
  /** Dotted path of the field the agent refused, for invalid_input. */
  field?: string;
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

const TITLES: Record<Reason, string> = {
  invalid_input: "Some details were rejected",
  not_found: "Venue not found",
  conflict: "Matches still to confirm",
  budget: "Today's Qloo budget is spent",
  busy: "Outdraw is busy",
  live_mode_off: "Live lookups are paused",
  unavailable: "Can't reach Outdraw",
  unknown: "Something went wrong",
};

/** Reasons a retry can clear; the rest need the booker to do something else first. */
export const RETRYABLE: ReadonlySet<Reason> = new Set<Reason>(["busy", "unavailable", "unknown"]);

/** Refusals of live analysis: a precomputed demo stands in. */
export const SAMPLED: ReadonlySet<Reason> = new Set<Reason>(["busy", "budget", "live_mode_off"]);

/** User-facing text for a `{reason}`. */
export function reasonText(reason: Reason): string {
  return MESSAGES[reason];
}

const sampleLink = (slug: SeedSlug): NextLink => ({
  label: "See a sample shortlist",
  href: `/v/${encodeURIComponent(slug)}`,
});

function nextLink(reason: Reason, details: ErrorDetails): NextLink | null {
  if (SAMPLED.has(reason)) return sampleLink(details.fallbackSeed ?? FALLBACK_SEED);
  switch (reason) {
    case "not_found":
      return { label: "Set up a venue", href: "/venue/new" };
    case "conflict":
      return { label: "Confirm the matches", href: "/venue/new" };
    case "invalid_input":
      return { label: "Edit the venue", href: "/venue/new" };
    default:
      return details.venueId
        ? { label: "Back to crowd profile", href: `/venue/${encodeURIComponent(details.venueId)}` }
        : { label: "Back to the start", href: "/" };
  }
}

/** The UI state for a `{reason}` and what came with it. */
export function errorState(reason: Reason, details: ErrorDetails = {}): ErrorState {
  const field = reason === "invalid_input" && details.field;
  return {
    reason,
    title: TITLES[reason],
    message: field ? `${MESSAGES[reason]} The field refused was ${field}.` : MESSAGES[reason],
    retryable: RETRYABLE.has(reason),
    link: nextLink(reason, details),
    pending: reason === "conflict" ? (details.pending ?? []) : [],
  };
}
