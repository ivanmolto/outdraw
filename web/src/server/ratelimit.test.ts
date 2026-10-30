import { createHash } from "node:crypto";

import type { Redis } from "@upstash/redis";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import nextConfig from "@/next.config";
import { NextRequest } from "next/server";

import { config, proxy } from "@/proxy";
import { createFakeRedis } from "./fake-redis.mjs";
import {
  checkPlanLimit,
  checkRateLimit,
  clientIp,
  createLimiters,
  createPlanLimiters,
  FALLBACK_SEED,
  isAgentWork,
  peekRateLimit,
  PLAN_LIMITS,
  remainingPlanRuns,
  remainingRuns,
  spendRateLimit,
  type Limiter,
  type PlanLimiters,
} from "./ratelimit";

const HOUR = Date.UTC(2026, 9, 25, 12);
const MINUTE = 60_000;

function req(path: string, { method = "POST", ip = "203.0.113.7" } = {}): Request {
  return new Request(`http://localhost${path}`, {
    method,
    headers: { "x-forwarded-for": `${ip}, 10.0.0.1` },
  });
}

let limiters: Limiter[];
let redis: ReturnType<typeof createFakeRedis>;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(HOUR);
  redis = createFakeRedis();
  limiters = createLimiters(redis.client as unknown as Redis);
});

afterEach(() => {
  vi.useRealTimers();
});

const run = (ip?: string) => checkRateLimit(req("/api/runs", { ip }), limiters);

describe("checkRateLimit", () => {
  it("gives the 11th run in an hour a 429", async () => {
    for (let i = 0; i < 10; i++) {
      vi.setSystemTime(HOUR + i * 5 * MINUTE);
      expect(await run(), `run ${i + 1}`).toBeNull();
    }
    vi.setSystemTime(HOUR + 55 * MINUTE);
    const res = await run();

    expect(res?.status).toBe(429);
    // The body names the window and the real wait, the same seconds as Retry-After, so the
    // UI can say "10 runs this hour, try again in N minutes" instead of "busy".
    const body = await res?.json();
    expect(body).toEqual({
      reason: "busy",
      fallback_seed: FALLBACK_SEED,
      limit: "hourly",
      runs: 10,
      retry_after: Number(res?.headers.get("retry-after")),
    });
    expect(body.retry_after).toBeGreaterThan(0);
    expect(body.retry_after).toBeLessThanOrEqual(60 * 60);
  });

  it("caps a burst at 3 a minute without spending the hourly allowance", async () => {
    for (let i = 0; i < 3; i++) expect(await run()).toBeNull();
    const res = await run();

    expect(res?.status).toBe(429);
    expect(Number(res?.headers.get("retry-after"))).toBeGreaterThan(0);
    // The blocked 4th never reached the hourly window.
    const hourly = [...redis.store].filter(([k]) => k.startsWith("outdraw:rl:hourly"));
    expect(hourly.map(([, e]) => e.value)).toEqual([3]);

    vi.setSystemTime(HOUR + 2 * MINUTE);
    expect(await run()).toBeNull();
  });

  it("limits each IP on its own, keyed by the first x-forwarded-for value", async () => {
    for (let i = 0; i < 3; i++) await run("198.51.100.1");
    expect((await run("198.51.100.1"))?.status).toBe(429);
    expect(await run("198.51.100.2")).toBeNull();
    expect([...redis.store.keys()].some((k) => k.includes("10.0.0.1"))).toBe(false);
  });

  it("counts resolve and profile but not reads, venue saves or confirms", async () => {
    await checkRateLimit(req("/api/venues/ven_1/resolve"), limiters);
    await checkRateLimit(req("/api/venues/ven_1/profile"), limiters);
    await run();
    expect((await run())?.status).toBe(429);

    for (const [path, method] of [
      ["/api/venues", "POST"],
      ["/api/venues/ven_1/confirm", "POST"],
      ["/api/venues/ven_1", "GET"],
      ["/api/runs/run_1", "GET"],
      ["/api/runs/run_1/compare", "GET"],
    ]) {
      expect(await checkRateLimit(req(path, { method }), limiters), path).toBeNull();
    }
  });

  it("lets requests through when Redis fails or limits are off", async () => {
    const broken: Limiter = { limit: () => Promise.reject(new Error("down")) };
    vi.spyOn(console, "error").mockImplementation(() => {});

    expect(await checkRateLimit(req("/api/runs"), [broken])).toBeNull();
    expect(await checkRateLimit(req("/api/runs"), null)).toBeNull();
    expect(await peekRateLimit(req("/api/x"), [broken])).toBeNull();
    expect(await peekRateLimit(req("/api/x"), null)).toBeNull();
    await expect(spendRateLimit(req("/api/x"), [broken])).resolves.toBeUndefined();
  });
});

describe("peekRateLimit and spendRateLimit", () => {
  const brief = req("/api/runs/run_1/candidates/E/brief", { method: "GET" });
  const used = () =>
    [...redis.store].filter(([k]) => k.startsWith("outdraw:rl:burst")).map(([, e]) => e.value);

  it("peeks without spending, and spends a token on demand", async () => {
    expect(await peekRateLimit(brief, limiters)).toBeNull();
    expect(used()).toEqual([0]);

    await spendRateLimit(brief, limiters);
    await spendRateLimit(brief, limiters);
    expect(used()).toEqual([2]);
    expect(await peekRateLimit(brief, limiters)).toBeNull();

    // The third token is the last: once spent, a peek refuses while a consume would too.
    await spendRateLimit(brief, limiters);
    const res = await peekRateLimit(brief, limiters);
    expect(res?.status).toBe(429);
    expect(await res?.json()).toEqual({
      reason: "busy",
      fallback_seed: FALLBACK_SEED,
      limit: "burst",
      runs: 3,
      retry_after: Number(res?.headers.get("retry-after")),
    });
    expect(Number(res?.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(used()).toEqual([3]);
    expect((await run())?.status).toBe(429);
  });

  it("shares the windows with the proxy's runs, per IP", async () => {
    for (let i = 0; i < 3; i++) expect(await run()).toBeNull();
    expect((await peekRateLimit(brief, limiters))?.status).toBe(429);
    expect(await peekRateLimit(req("/api/x", { ip: "198.51.100.9" }), limiters)).toBeNull();

    vi.setSystemTime(HOUR + 2 * MINUTE);
    expect(await peekRateLimit(brief, limiters)).toBeNull();
  });
});

describe("remainingRuns", () => {
  it("reads the hourly runs left without spending one", async () => {
    const me = req("/api/me", { method: "GET" });
    expect(await remainingRuns(me, limiters)).toBe(10);
    expect(await remainingRuns(me, limiters)).toBe(10);
    await checkRateLimit(req("/api/runs"), limiters);
    await checkRateLimit(req("/api/runs"), limiters);
    expect(await remainingRuns(me, limiters)).toBe(8);
  });

  it("is null while limits are off or Redis fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const broken: Limiter = { limit: async () => Promise.reject(new Error("redis down")) };
    expect(await remainingRuns(req("/api/me"), null)).toBeNull();
    expect(await remainingRuns(req("/api/me"), [broken])).toBeNull();
  });
});

describe("plan limits", () => {
  const DAY = 24 * 60 * MINUTE;
  const SESSION = "ses_abcdefghijklmnopqrstuvwxyz01";
  let plans: PlanLimiters;

  beforeEach(() => {
    plans = createPlanLimiters(redis.client as unknown as Redis);
  });

  it("gives a free session 3 runs a day and refuses the 4th, naming the plan", async () => {
    for (let i = 0; i < 3; i++) {
      vi.setSystemTime(HOUR + i * 60 * MINUTE);
      expect(await checkPlanLimit(SESSION, "free", plans), `run ${i + 1}`).toBeNull();
    }
    const res = await checkPlanLimit(SESSION, "free", plans);

    expect(res?.status).toBe(429);
    expect(await res?.json()).toEqual({
      reason: "plan_limit",
      plan: "free",
      runs_per_day: 3,
      fallback_seed: FALLBACK_SEED,
    });
    expect(Number(res?.headers.get("retry-after"))).toBeGreaterThan(0);

    // A day on, the window has moved past them.
    vi.setSystemTime(HOUR + DAY + 3 * 60 * MINUTE);
    expect(await checkPlanLimit(SESSION, "free", plans)).toBeNull();
  });

  it("lets a pro session carry on where a free one stops, on its own counter", async () => {
    for (let i = 0; i < 3; i++) await checkPlanLimit(SESSION, "free", plans);
    expect((await checkPlanLimit(SESSION, "free", plans))?.status).toBe(429);

    for (let i = 0; i < PLAN_LIMITS.pro.tokens; i++) {
      expect(await checkPlanLimit(SESSION, "pro", plans), `pro run ${i + 1}`).toBeNull();
    }
    const res = await checkPlanLimit(SESSION, "pro", plans);
    expect(res?.status).toBe(429);
    expect(await res?.json()).toMatchObject({ reason: "plan_limit", plan: "pro", runs_per_day: 30 });
  });

  it("counts each session on its own, apart from the IP windows", async () => {
    for (let i = 0; i < 3; i++) await checkPlanLimit(SESSION, "free", plans);
    expect(await checkPlanLimit("ses_other", "free", plans)).toBeNull();
    expect(await run()).toBeNull();
    const keys = [...redis.store.keys()];
    expect(keys.some((k) => k.startsWith(`outdraw:rl:day:free:${SESSION}:`))).toBe(true);
    expect(keys.some((k) => k.startsWith("outdraw:rl:hourly:") && k.includes(SESSION))).toBe(false);
  });

  it("reads the runs left today without spending one", async () => {
    expect(await remainingPlanRuns(SESSION, "free", plans)).toBe(3);
    expect(await remainingPlanRuns(SESSION, "free", plans)).toBe(3);
    await checkPlanLimit(SESSION, "free", plans);
    expect(await remainingPlanRuns(SESSION, "free", plans)).toBe(2);
    expect(await remainingPlanRuns(SESSION, "pro", plans)).toBe(30);
  });

  it("lets runs through when Redis fails or limits are off", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const broken: Limiter = { limit: () => Promise.reject(new Error("down")) };
    const down: PlanLimiters = { free: broken, pro: broken };
    expect(await checkPlanLimit(SESSION, "free", down)).toBeNull();
    expect(await checkPlanLimit(SESSION, "free", null)).toBeNull();
    expect(await remainingPlanRuns(SESSION, "free", down)).toBeNull();
    expect(await remainingPlanRuns(SESSION, "free", null)).toBeNull();
  });
});

describe("fake-redis.mjs", () => {
  /** SHA-1 of the sliding-window Lua script @upstash/ratelimit 2.2.0 sends to Redis. */
  const SCRIPT_SHA1 = "74740bb3d7792848093cdef586b6b6037c455fd6";

  it("mirrors the Lua script the pinned library sends", async () => {
    // fake-redis.mjs re-implements that script in JS. A library bump that changes the
    // script (its keys, args or reply) would leave the fake answering a different question,
    // so pin the script here: when this fails, re-read the script and update the fake.
    let script = "";
    let keys: string[] = [];
    let args: (string | number)[] = [];
    const redis = {
      evalsha: async () => {
        throw new Error("NOSCRIPT No matching script");
      },
      eval: async (s: string, k: string[], a: (string | number)[]) => {
        [script, keys, args] = [s, k, a];
        return [2, 3];
      },
    };
    const [burst] = createLimiters(redis as unknown as Redis);
    await burst.limit("203.0.113.7");

    expect(createHash("sha1").update(script).digest("hex")).toBe(SCRIPT_SHA1);
    expect(keys).toEqual([
      `outdraw:rl:burst:203.0.113.7:${Math.floor(HOUR / MINUTE)}`,
      `outdraw:rl:burst:203.0.113.7:${Math.floor(HOUR / MINUTE) - 1}`,
    ]);
    expect(args).toEqual([3, HOUR, MINUTE, 1]);
  });
});

describe("isAgentWork", () => {
  it.each([
    ["POST", "/api/runs", true],
    ["POST", "/api/runs/run_1/refine", true],
    ["POST", "/api/venues/ven_1/resolve", true],
    ["POST", "/api/venues/ven_1/profile", true],
    ["POST", "/api/venues", false],
    ["POST", "/api/venues/ven_1/confirm", false],
    ["GET", "/api/runs/run_1", false],
    // The brief route counts itself (peek, then spend when the agent drafted).
    ["GET", "/api/runs/run_1/candidates/urn%3Aentity%3Ax/brief", false],
    ["PATCH", "/api/runs/run_1/candidates/urn%3Aentity%3Ax", false],
    ["POST", "/api/runsx", false],
  ])("%s %s → %s", (method, path, expected) => {
    expect(isAgentWork(method, path)).toBe(expected);
  });
});

describe("clientIp", () => {
  it("falls back to a shared bucket without the header", () => {
    expect(clientIp(new Headers())).toBe("unknown");
    expect(clientIp(new Headers({ "x-forwarded-for": " 192.0.2.4 ,10.0.0.1" }))).toBe("192.0.2.4");
  });
});

describe("proxy matcher", () => {
  it.each([
    ["/api/runs", true],
    ["/api/runs/run_1/compare", true],
    ["/api/venues/ven_1/resolve", true],
    ["/v/nola-jazz", true],
    ["/v/nope", true],
    ["/v/nola-jazz/deeper", false],
    ["/", false],
    ["/venue/ven_1/shortlist", false],
    ["/api/health", false],
  ])("%s → %s", (url, expected) => {
    expect(unstable_doesMiddlewareMatch({ config, nextConfig, url })).toBe(expected);
  });
});

describe("demo pages in the proxy", () => {
  // No Redis env: a seed page must pass without touching the limiter anyway.
  const request = (path: string) => new NextRequest(new URL(path, "http://web.test"));

  it("lets a seed slug through, never limited", async () => {
    const res = await proxy(request("/v/nola-jazz"));
    expect(res.status).toBe(200);
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("answers any other slug with the not-found page as a 404", async () => {
    for (const path of ["/v/nope", "/v/Not%20A%20Slug", "/v/NOLA-JAZZ"]) {
      const res = await proxy(request(path));
      expect(res.status, path).toBe(404);
      expect(res.headers.get("x-middleware-rewrite"), path).toBe("http://web.test/_not-found");
    }
  });
});
