import { createHash } from "node:crypto";

import type { Redis } from "@upstash/redis";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import nextConfig from "@/next.config";
import { config } from "@/proxy";
import { createFakeRedis } from "./fake-redis.mjs";
import {
  checkRateLimit,
  clientIp,
  createLimiters,
  FALLBACK_SEED,
  isAgentWork,
  peekRateLimit,
  spendRateLimit,
  type Limiter,
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
    expect(await res?.json()).toEqual({ reason: "busy", fallback_seed: FALLBACK_SEED });
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
    ["/v/nyc-jazz", false],
    ["/", false],
    ["/venue/ven_1/shortlist", false],
    ["/api/health", false],
  ])("%s → %s", (url, expected) => {
    expect(unstable_doesMiddlewareMatch({ config, nextConfig, url })).toBe(expected);
  });
});
