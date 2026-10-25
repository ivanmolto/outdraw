// Per-IP limits on agent work, checked by /proxy.ts before a request reaches the /api
// routes: 10 runs an hour plus a burst cap of 3 a minute, both sliding windows in Upstash
// Redis. A "run" is any request that makes the agent spend Qloo or LLM calls; reads, venue
// saves and match confirmations pass free, and seed pages are never matched by the proxy.
// One read can spend: the first brief per act drafts it (a Qloo call and a model call).
// The proxy cannot tell a draft from the agent's cached copy, so the brief route limits
// itself: `peekRateLimit` before the call, `spendRateLimit` after it when `cached` is false.
// No `server-only` here: the proxy is not bundled with the react-server condition.
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

import type { ErrorBody } from "./proxy";

/** The demo venue the UI falls back to when live analysis is limited. */
export const FALLBACK_SEED = "nyc-jazz";

export const LIMITS = {
  burst: { tokens: 3, window: "1 m" },
  hourly: { tokens: 10, window: "1 h" },
} as const;

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
  return (["burst", "hourly"] as const).map(
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

let shared: Limiter[] | null | undefined;

/** Limiters from UPSTASH_REDIS_REST_URL/TOKEN, or null (limits off) when they are unset. */
function defaultLimiters(): Limiter[] | null {
  if (shared === undefined) {
    const configured = process.env.UPSTASH_REDIS_REST_URL && process.env.UPSTASH_REDIS_REST_TOKEN;
    if (!configured) console.warn("rate limits off: UPSTASH_REDIS_REST_URL/TOKEN not set");
    shared = configured ? createLimiters(Redis.fromEnv()) : null;
  }
  return shared;
}

export type BusyBody = ErrorBody & { fallback_seed: string };

function busy(reset: number): Response {
  const body: BusyBody = { reason: "busy", fallback_seed: FALLBACK_SEED };
  const retryAfter = Math.max(1, Math.ceil((reset - Date.now()) / 1000));
  return Response.json(body, {
    status: 429,
    headers: { "retry-after": String(retryAfter) },
  });
}

/** The 429 for `req`'s IP when it is over a limit, else null. `rate` 1 takes a token; 0
 * only reads the windows, and is over the limit when no token is left (the script's
 * zero-rate reply counts a full window as success, so `remaining` is what decides). */
async function enforce(req: Request, limiters: Limiter[], rate: number): Promise<Response | null> {
  const ip = clientIp(req.headers);
  try {
    for (const limiter of limiters) {
      const { success, remaining, reset } = await limiter.limit(ip, { rate });
      if (rate > 0 ? !success : remaining <= 0) return busy(reset);
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

/** Take one token for `req`'s IP after the fact. Never blocks: the peek before the call is
 * the gate, and two first requests racing past it cost one more call, not a refusal. */
export async function spendRateLimit(
  req: Request,
  limiters: Limiter[] | null = defaultLimiters(),
): Promise<void> {
  if (limiters) await enforce(req, limiters, 1);
}
