import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { remainingRuns } from "@/src/server/ratelimit";
import { SESSION_COOKIE } from "@/src/server/session";
import { AGENT_URL, fakeAgent } from "@/src/server/test-agent";
import { GET } from "./route";

// The cookie jar of the request: `cookies()` needs a Next request scope vitest has none of.
const jar = vi.hoisted(() => {
  const values = new Map<string, string>();
  const set = vi.fn((name: string, value: string, opts?: Record<string, unknown>) => {
    void opts;
    values.set(name, value);
  });
  const get = (name: string) => (values.has(name) ? { value: values.get(name) } : undefined);
  return { values, set, get };
});
vi.mock("next/headers", () => ({ cookies: async () => jar }));

vi.mock("@/src/server/ratelimit", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/src/server/ratelimit")>()),
  remainingRuns: vi.fn(async () => 7),
}));

const SESSION = "ses_abcdefghijklmnopqrstuvwxyz01";
const req = () => new Request("http://web.test/api/me");

beforeEach(() => {
  jar.values.clear();
  jar.set.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/me", () => {
  it("makes a free session on the first visit and sets its httpOnly cookie", async () => {
    vi.stubEnv("HACKATHON_MODE", "false");
    const sent = fakeAgent(Response.json({ id: SESSION, plan: "free" }, { status: 201 }));

    const res = await GET(req());

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({
      plan: "free",
      stored_plan: "free",
      hackathon: false,
      remaining_runs: 7,
      runs_per_hour: 10,
    });
    expect(Object.values(body.features).every((on) => on === false)).toBe(true);
    expect(JSON.stringify(body)).not.toContain(SESSION);
    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([`POST ${AGENT_URL}/sessions`]);
    expect(jar.set).toHaveBeenCalledWith(
      SESSION_COOKIE,
      SESSION,
      expect.objectContaining({ httpOnly: true, sameSite: "lax", path: "/" }),
    );
  });

  it("reads the cookie's session back, and grants pro in hackathon mode", async () => {
    vi.stubEnv("HACKATHON_MODE", "true");
    jar.values.set(SESSION_COOKIE, SESSION);
    const sent = fakeAgent(Response.json({ id: SESSION, plan: "free" }));

    const body = await (await GET(req())).json();

    expect(body).toMatchObject({ plan: "pro", stored_plan: "free", hackathon: true });
    expect(Object.values(body.features).every((on) => on === true)).toBe(true);
    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([
      `GET ${AGENT_URL}/sessions/${SESSION}`,
    ]);
    expect(jar.set).not.toHaveBeenCalled();
  });

  it("replaces a session the agent no longer knows", async () => {
    jar.values.set(SESSION_COOKIE, "ses_gone");
    const sent = fakeAgent(
      Response.json({ detail: "Unknown session." }, { status: 404 }),
      Response.json({ id: SESSION, plan: "free" }, { status: 201 }),
    );

    expect((await GET(req())).status).toBe(200);
    expect(sent.map((s) => s.method)).toEqual(["GET", "POST"]);
    expect(jar.values.get(SESSION_COOKIE)).toBe(SESSION);
  });

  it("never sends a malformed cookie to the agent", async () => {
    jar.values.set(SESSION_COOKIE, "../venues/x");
    const sent = fakeAgent(Response.json({ id: SESSION, plan: "free" }, { status: 201 }));

    await GET(req());

    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([`POST ${AGENT_URL}/sessions`]);
  });

  it("answers unavailable when the agent is down", async () => {
    fakeAgent(new TypeError("fetch failed"));
    const res = await GET(req());
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ reason: "unavailable" });
    expect(remainingRuns).toHaveBeenCalled();
  });
});
