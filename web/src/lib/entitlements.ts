// Free vs Pro (T46): which features a plan unlocks, and what `GET /api/me` returns. Pure
// and browser-safe: the server decides the plan (src/server/session.ts), the UI only reads
// `features` to badge and hide. HACKATHON_MODE=true lifts everyone to Pro, so nothing is
// blocked while the hackathon is judged; the stored plan stays what was paid for.

export const PLANS = ["free", "pro"] as const;
export type Plan = (typeof PLANS)[number];

/** Every Pro feature and its label, in the order the pricing page lists them. */
export const PRO_FEATURES = {
  multi_venue: "More than one venue",
  full_shortlist: "Shortlist beyond the top 5",
  briefs: "Booking briefs and outreach",
  exports: "Exports to your booking tool",
  run_history: "Run history",
} as const;

export type ProFeature = keyof typeof PRO_FEATURES;
export type Features = Record<ProFeature, boolean>;

/** Acts a free plan sees on a shortlist; the rest are Pro. */
export const FREE_SHORTLIST = 5;

/** Shortlist runs (fresh or refined) a session gets in a day, per plan (T48). The server
 * counts them (src/server/ratelimit.ts); the pricing page and the limit message quote them. */
export const RUNS_PER_DAY: Record<Plan, number> = { free: 3, pro: 30 };

/** Venue Pro, a month, in US dollars: under Gigwell's listed $99 (docs/briefing.md). */
export const PRO_PRICE_USD = 39;

export const HACKATHON_BANNER = "All Pro features free during the Qloo hackathon";

/** Whether HACKATHON_MODE is on: only the exact word `true` (any case) turns it on. */
export function hackathonMode(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "true";
}

/** The plan a session gets: its stored plan, or Pro for everyone in hackathon mode. */
export function effectivePlan(stored: Plan, hackathon: boolean): Plan {
  return hackathon ? "pro" : stored;
}

/** What `plan` unlocks: Pro unlocks everything, free none of the Pro features. */
export function entitlements(plan: Plan): Features {
  const on = plan === "pro";
  return Object.fromEntries(Object.keys(PRO_FEATURES).map((f) => [f, on])) as Features;
}

/** `GET /api/me`. The session id stays in its httpOnly cookie and is never in the body. */
export interface Me {
  /** The plan in force: what the UI branches on. */
  plan: Plan;
  /** The plan the session paid for, Pro only after checkout (T47). */
  stored_plan: Plan;
  hackathon: boolean;
  features: Features;
  /** Runs left now: the smaller of this IP's hourly and this plan's daily counts; null
   * while rate limits are off. */
  remaining_runs: number | null;
  /** The IP's hourly cap and the plan's daily allowance (T48). */
  runs_per_hour: number;
  runs_per_day: number;
}

/** The runs left: the tighter of the two counts, or null when neither is known. */
export function remainingOf(hourly: number | null, daily: number | null): number | null {
  if (hourly === null) return daily;
  if (daily === null) return hourly;
  return Math.min(hourly, daily);
}

export function me(
  stored: Plan,
  hackathon: boolean,
  remainingRuns: number | null,
  runsPerHour: number,
  runsPerDay: number,
): Me {
  const plan = effectivePlan(stored, hackathon);
  return {
    plan,
    stored_plan: stored,
    hackathon,
    features: entitlements(plan),
    remaining_runs: remainingRuns,
    runs_per_hour: runsPerHour,
    runs_per_day: runsPerDay,
  };
}

/** Whether to show `feature`: while the plan is unknown (loading, or /api/me failed) it is
 * shown, so a slow or missing read never blocks a feature. */
export function shows(me: Pick<Me, "features"> | null, feature: ProFeature): boolean {
  return me === null || me.features[feature];
}
