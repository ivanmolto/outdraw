// The per-IP limits in proxy.ts against the fake Upstash in e2e/mock-redis.mjs: the 4th run
// in a minute gets 429 {reason:"busy"} without reaching the agent, the shortlist page
// shows the busy message, and pages outside the matcher stay reachable. Each test sets its
// own x-forwarded-for, so buckets never carry over between tests or retries.
import { expect, test, type APIRequestContext } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const VENUE_ID = "ven_fixture";

const RUN = {
  venue_id: VENUE_ID,
  constraints: {
    include_tags: [],
    exclude_tags: [],
    size_tier: "emerging",
    exclude_recent_months: 0,
    max_candidates: 3,
  },
  message: "",
};

/** A documentation-range IP unique to this test attempt. */
function ipFor(info: { workerIndex: number; retry: number }, base: number): string {
  return `198.51.100.${base + info.retry * 10 + info.workerIndex}`;
}

async function agentRuns(request: APIRequestContext): Promise<number> {
  const all = (await (await request.get(`${AGENT}/__requests`)).json()) as {
    method: string;
    path: string;
  }[];
  return all.filter((r) => r.method === "POST" && r.path === "/runs").length;
}

test("the 4th run in a minute gets 429 and never reaches the agent", async ({ request }, info) => {
  const headers = { "x-forwarded-for": `${ipFor(info, 1)}, 10.0.0.1` };

  for (let i = 0; i < 3; i++) {
    const res = await request.post("/api/runs", { data: RUN, headers });
    expect(res.status(), `run ${i + 1}`).not.toBe(429);
  }
  const before = await agentRuns(request);
  const res = await request.post("/api/runs", { data: RUN, headers });

  expect(res.status()).toBe(429);
  expect(await res.json()).toEqual({ reason: "busy", fallback_seed: "nyc-jazz" });
  expect(Number(res.headers()["retry-after"])).toBeGreaterThan(0);
  expect(await agentRuns(request)).toBe(before);

  // Another visitor is unaffected, and so are pages and reads for the limited one.
  const other = await request.post("/api/runs", {
    data: RUN,
    headers: { "x-forwarded-for": ipFor(info, 101) },
  });
  expect(other.status()).not.toBe(429);
  for (const path of ["/v/nyc-jazz", "/venue/new", "/api/health"]) {
    expect((await request.get(path, { headers })).status(), path).not.toBe(429);
  }
});

test("a limited visitor sees the busy message on the shortlist", async ({ page, request }, info) => {
  const ip = ipFor(info, 51);
  const headers = { "x-forwarded-for": ip };
  // A venue with a profile for the shortlist page; the profile build counts as one run.
  await request.post("/api/venues", {
    data: { name: "Synthetic Room", city: "New York", country: "US", capacity: 150, past_shows: [] },
    headers,
  });
  expect((await request.post(`/api/venues/${VENUE_ID}/profile`, { headers })).status()).toBe(200);
  expect((await request.post("/api/runs", { data: RUN, headers })).status()).not.toBe(429);
  expect((await request.post("/api/runs", { data: RUN, headers })).status()).not.toBe(429);

  await page.setExtraHTTPHeaders(headers);
  const limited = page.waitForResponse((r) => r.url().endsWith("/api/runs"));
  await page.goto(`/venue/${VENUE_ID}/shortlist`);

  expect((await limited).status()).toBe(429);
  await expect(page.getByText("Outdraw is busy with another request.")).toBeVisible();
});
