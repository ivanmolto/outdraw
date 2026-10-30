// Per-IP limits on agent work, checked by /proxy.ts before a request reaches the /api
// routes: 10 runs an hour plus a burst cap of 3 a minute, both sliding windows in Upstash
// Redis. A "run" is any request that makes the agent spend Qloo or LLM calls; reads, venue
// saves and match confirmations pass free, and seed pages are never matched by the proxy.
// One read can spend: the first brief per act drafts it (a Qloo call and a model call).
// The proxy cannot tell a draft from the agent's cached copy, so the brief route limits
// itself: `peekRateLimit` before the call, `spendRateLimit` after it when `cached` is false.
// Under those, the plan's daily run allowance per session (T48): free 3 a day, pro 30, a
// sliding day keyed by the session id, checked by the run routes themselves (they hold the
// session, the proxy does not) through `checkPlanLimit`, and read by `GET /api/me`.
// No `server-only` here: the proxy is not bundled with the react-server condition.
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

import { PLANS, RUNS_PER_DAY, type Plan } from "@/src/lib/entitlements";
import { FALLBACK_SEED } from "@/src/lib/seeds";
import type { ErrorBody } from "./proxy";

export { FALLBACK_SEED };

export const LIMITS = {
  burst: { tokens: 3, window: "1 m" },
  hourly: { tokens: 10, window: "1 h" },
} as const;

export type LimitName = keyof typeof LIMITS;

/** The order `createLimiters` returns them in: burst first, so a burst never spends hourly. */
const LIMIT_ORDER: readonly LimitName[] = ["burst", "hourly"];

/** Runs a session gets in a sliding day, per plan: both under the per-IP hourly cap and
 * the agent's daily budget, so a plan never buys past the global caps. */
export const PLAN_LIMITS: Record<Plan, { tokens: number; window: "1 d" }> = {
  free: { tokens: RUNS_PER_DAY.free, window: "1 d" },
  pro: { tokens: RUNS_PER_DAY.pro, window: "1 d" },
};

/** POSTs under the limited prefixes that only write our own database, never Qloo or Claude. */
const FREE_POSTS = [/^\/api\/venues\/?$/, /^\/api\/venues\/[^/]+\/confirm\/?$/];

/** Whether `method pathname` makes the agent call Qloo or the model (and so counts in the
 * proxy). The brief GET is not here: its route counts it itself, see `peekRateLimit`. */
export function isAgentWork(method: string, pathname: string): boolean {
  if (method !== "POST") return false;
  if (!/^\/api\/(runs|venues)(\/|$)/.test(pathname)) return false;
  return !FREE_POSTS.some((re) => re.test(pathname));
}

/** The client IP: the first `x-forwarded-for` value (Vercel sets the header itself). */
export function clientIp(headers: Headers): string {
  return headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
}

export interface Limiter {
  /** `rate` is the tokens to take: 1 by default; 0 reads the window without spending. */
  limit(
    identifier: string,
    opts?: { rate?: number },
  ): Promise<{ success: boolean; remaining: number; reset: number }>;
}

/** The burst and hourly limiters over `redis`, burst first so a burst never spends hourly. */
export function createLimiters(redis: Redis): Limiter[] {
  return LIMIT_ORDER.map(
    (name) =>
      new Ratelimit({
        redis,
        prefix: `outdraw:rl:${name}`,
        limiter: Ratelimit.slidingWindow(LIMITS[name].tokens, LIMITS[name].window),
        // On a slow Redis let the request through: the agent's daily budget still caps cost.
        timeout: 1000,
      }),
  );
}

export type PlanLimiters = Record<Plan, Limiter>;

/** One daily limiter per plan over `redis`, keyed by session id. */
export function createPlanLimiters(redis: Redis): PlanLimiters {
  return Object.fromEntries(
    PLANS.map((plan) => [
      plan,
      new Ratelimit({
        redis,
        prefix: `outdraw:rl:day:${plan}`,
        limiter: Ratelimit.slidingWindow(PLAN_LIMITS[plan].tokens, PLAN_LIMITS[plan].window),
        timeout: 1000,
      }),
    ]),
  ) as unknown as PlanLimiters;
}

let shared: { ip: Limiter[]; plan: PlanLimiters } | null | undefined;

/** Limiters from UPSTASH_REDIS_REST_URL/TOKEN, or null (limits off) when they are unset. */
function defaults(): { ip: Limiter[]; plan: PlanLimiters } | null {
  if (shared === undefined) {
    const configured = process.env.UPSTASH_REDIS_REST_URL && process.env.UPSTASH_REDIS_REST_TOKEN;
    if (!configured) console.warn("rate limits off: UPSTASH_REDIS_REST_URL/TOKEN not set");
    if (configured) {
      const redis = Redis.fromEnv();
      shared = { ip: createLimiters(redis), plan: createPlanLimiters(redis) };
    } else shared = null;
  }
  return shared;
}

const defaultLimiters = (): Limiter[] | null => defaults()?.ip ?? null;
const defaultPlanLimiters = (): PlanLimiters | null => defaults()?.plan ?? null;

export type BusyBody = ErrorBody & Required<Pick<ErrorBody, "fallback_seed">>;

/** Seconds until `reset`, at least 1: the Retry-After header. */
function secondsUntil(reset: number): number {
  return Math.max(1, Math.ceil((reset - Date.now()) / 1000));
}

function refuse(body: ErrorBody, reset: number): Response {
  return Response.json(body, {
    status: 429,
    headers: { "retry-after": String(secondsUntil(reset)) },
  });
}

/** The IP limit's 429: which window tripped and the real wait, so the UI can say "10 runs
 * this hour, try again in 12 minutes" instead of "busy, try again in a moment". */
function busy(limit: LimitName, reset: number): Response {
  const body: BusyBody = {
    reason: "busy",
    fallback_seed: FALLBACK_SEED,
    limit,
    runs: LIMITS[limit].tokens,
    retry_after: secondsUntil(reset),
  };
  return refuse(body, reset);
}

/** The 429 for `req`'s IP when it is over a limit, else null. `rate` 1 takes a token; 0
 * only reads the windows, and is over the limit when no token is left (the script's
 * zero-rate reply counts a full window as success, so `remaining` is what decides). */
async function enforce(req: Request, limiters: Limiter[], rate: number): Promise<Response | null> {
  const ip = clientIp(req.headers);
  try {
    for (const [i, limiter] of limiters.entries()) {
      const { success, remaining, reset } = await limiter.limit(ip, { rate });
      if (rate > 0 ? !success : remaining <= 0) return busy(LIMIT_ORDER[i] ?? "hourly", reset);
    }
  } catch (err) {
    // Fail open: a Redis outage must not take the site down.
    console.error("rate limit check failed:", err);
  }
  return null;
}

/** A 429 for `req` when its IP is over a limit, otherwise null (let it through). */
export async function checkRateLimit(
  req: Request,
  limiters: Limiter[] | null = defaultLimiters(),
): Promise<Response | null> {
  if (!limiters || !isAgentWork(req.method, new URL(req.url).pathname)) return null;
  return enforce(req, limiters, 1);
}

/** A 429 when `req`'s IP has no token left, without taking one: for a route that only
 * knows afterwards whether the agent spent anything (the brief). */
export async function peekRateLimit(
  req: Request,
  limiters: Limiter[] | null = defaultLimiters(),
): Promise<Response | null> {
  return limiters ? enforce(req, limiters, 0) : null;
}

/** Runs `req`'s IP has left in the hourly window, without taking one (for `GET /api/me`);
 * null while limits are off or Redis fails. The hourly limiter is last in createLimiters. */
export async function remainingRuns(
  req: Request,
  limiters: Limiter[] | null = defaultLimiters(),
): Promise<number | null> {
  const hourly = limiters?.at(-1);
  if (!hourly) return null;
  try {
    return (await hourly.limit(clientIp(req.headers), { rate: 0 })).remaining;
  } catch (err) {
    console.error("rate limit read failed:", err);
    return null;
  }
}

/** Take one token for `req`'s IP after the fact. Never blocks: the peek before the call is
 * the gate, and two first requests racing past it cost one more call, not a refusal. */
export async function spendRateLimit(
  req: Request,
  limiters: Limiter[] | null = defaultLimiters(),
): Promise<void> {
  if (limiters) await enforce(req, limiters, 1);
}

/** The 429 `plan_limit` when session `id` on `plan` has no run left today, else null (and
 * a run is taken). The body names the plan and its allowance, so the UI can say which plan
 * ran out, and the demo venue to show instead. Fails open like the IP limits. */
export async function checkPlanLimit(
  id: string,
  plan: Plan,
  limiters: PlanLimiters | null = defaultPlanLimiters(),
): Promise<Response | null> {
  if (!limiters) return null;
  try {
    const { success, reset } = await limiters[plan].limit(id);
    if (!success) {
      return refuse(
        {
          reason: "plan_limit",
          plan,
          runs_per_day: PLAN_LIMITS[plan].tokens,
          fallback_seed: FALLBACK_SEED,
        },
        reset,
      );
    }
  } catch (err) {
    console.error("plan limit check failed:", err);
  }
  return null;
}

/** Runs session `id` has left today on `plan`, without taking one (for `GET /api/me`);
 * null while limits are off or Redis fails. */
export async function remainingPlanRuns(
  id: string,
  plan: Plan,
  limiters: PlanLimiters | null = defaultPlanLimiters(),
): Promise<number | null> {
  if (!limiters) return null;
  try {
    return (await limiters[plan].limit(id, { rate: 0 })).remaining;
  } catch (err) {
    console.error("plan limit read failed:", err);
    return null;
  }
}
