// Every `{reason}` the /api routes emit (src/server/proxy.ts, from AgentError in
// src/server/agent.ts), mapped to what the booker sees: a title, a message, whether a retry
// can help and where to go next. The one table for the whole UI, so a reason reads the same
// on a page, in a panel and under a button. Nothing here comes from the backend: never a
// message, URL or stack of its own.
import type { Reason } from "@/types/agent";
import { RUNS_PER_DAY, type Plan } from "./entitlements";
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
  /** From a 429 plan_limit: the plan that ran out today and its daily allowance. */
  plan?: Plan;
  runsPerDay?: number;
  /** From a 502 qloo_error: the Qloo error code and whether a retry can help. */
  code?: string;
  retryable?: boolean;
}

/** Qloo error codes that mean Outdraw's own Qloo setup is broken (the key, the harness,
 * its MCP server): nothing the booker does helps, and a retry would fail the same way. */
const OUR_QLOO_CODES: ReadonlySet<string> = new Set([
  "QLOO_AUTH",
  "harness_missing",
  "mcp_start_failed",
  "mcp_not_ready",
  "mcp_tools_missing",
  "mcp_not_connected",
]);

/** The qloo_error message for what the body said: a vetted sentence per case, never
 * Qloo's own `recovery` text (it can quote a path or a config file). */
function qlooErrorMessage({ code, retryable }: ErrorDetails): string {
  if (code && OUR_QLOO_CODES.has(code)) {
    return "Outdraw's connection to Qloo is not set up right. This is on our side; try a demo venue meanwhile.";
  }
  if (retryable) return "Qloo didn't answer in time. Try again in a moment.";
  return MESSAGES.qloo_error;
}

const MESSAGES: Record<Reason, string> = {
  invalid_input: "Some details were rejected. Check the highlighted fields.",
  not_found: "That venue no longer exists. Start again.",
  conflict: "Confirm every Qloo match before building the profile.",
  budget: "Outdraw has used today's Qloo budget. Try again tomorrow.",
  busy: "Outdraw is busy with another request. Try again in a moment.",
  live_mode_off: "Live Qloo lookups are switched off right now.",
  plan_limit: "This plan's runs for today are used up. Try again tomorrow.",
  qloo_error: "Qloo couldn't answer this request.",
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
  plan_limit: "Today's runs are used up",
  qloo_error: "Qloo couldn't answer",
  unavailable: "Can't reach Outdraw",
  unknown: "Something went wrong",
};

/** The plan_limit message for the plan that ran out: Free points at Venue Pro, Pro at
 * tomorrow. Without the details (an older body), the plain message. */
function planLimitMessage({ plan, runsPerDay }: ErrorDetails): string {
  if (!plan || runsPerDay == null) return MESSAGES.plan_limit;
  const used = `You've used the ${plan === "pro" ? "Pro" : "Free"} plan's ${runsPerDay} runs for today.`;
  return plan === "pro"
    ? `${used} Try again tomorrow.`
    : `${used} Venue Pro gets ${RUNS_PER_DAY.pro} a day.`;
}

/** Reasons a retry can clear; the rest need the booker to do something else first. A
 * qloo_error is retryable only when its body says so (`errorState` reads the details). */
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
    case "plan_limit":
      // A free plan has an upgrade to look at; a pro plan is at the top already.
      return details.plan === "pro"
        ? sampleLink(details.fallbackSeed ?? FALLBACK_SEED)
        : { label: "See the plans", href: "/pricing" };
    case "not_found":
      return { label: "Set up a venue", href: "/venue/new" };
    case "conflict":
      return { label: "Confirm the matches", href: "/venue/new" };
    case "invalid_input":
      return { label: "Edit the venue", href: "/venue/new" };
    case "qloo_error":
      // Our setup is broken: a demo stands in, as for a refusal. Otherwise, the way back.
      if (details.code && OUR_QLOO_CODES.has(details.code)) return sampleLink(FALLBACK_SEED);
      return details.venueId
        ? { label: "Back to crowd profile", href: `/venue/${encodeURIComponent(details.venueId)}` }
        : { label: "Back to the start", href: "/" };
    default:
      return details.venueId
        ? { label: "Back to crowd profile", href: `/venue/${encodeURIComponent(details.venueId)}` }
        : { label: "Back to the start", href: "/" };
  }
}

/** The UI state for a `{reason}` and what came with it. */
export function errorState(reason: Reason, details: ErrorDetails = {}): ErrorState {
  const field = reason === "invalid_input" && details.field;
  const message =
    reason === "plan_limit"
      ? planLimitMessage(details)
      : reason === "qloo_error"
        ? qlooErrorMessage(details)
        : field
          ? `${MESSAGES[reason]} The field refused was ${field}.`
          : MESSAGES[reason];
  return {
    reason,
    title: TITLES[reason],
    message,
    retryable: reason === "qloo_error" ? details.retryable === true : RETRYABLE.has(reason),
    link: nextLink(reason, details),
    pending: reason === "conflict" ? (details.pending ?? []) : [],
  };
}
