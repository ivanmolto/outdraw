// Tiered run limits (T48), on the `free` project's server (HACKATHON_MODE=false): a free
// session gets 3 runs a day and the 4th is 429 {reason:"plan_limit"} that never reaches the
// agent; the refine box shows the state with a link to the plans; and a session flipped to
// pro (through the agent's PATCH, as a payment webhook will) carries on. Each request comes
// from its own IP so the per-IP burst (3 a minute) never fires first: the session cookie,
// not the IP, is what the plan counts.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const KEY = "e2e-internal-key";

/** The venue input the mock stores, as the venue flow posts it. */
const VENUE_INPUT = JSON.parse(
  readFileSync(join(__dirname, "../app/venue/[id]/fixtures/venue.json"), "utf8"),
).venue;

const RUN = {
  venue_id: "ven_fixture",
  constraints: {
    include_tags: [],
    exclude_tags: [],
    size_tier: "emerging",
    exclude_recent_months: 0,
    max_candidates: 3,
  },
  message: "",
};

/** The venue the mock answers runs for, stored once (a free POST, outside the limits). */
async function storeVenue(request: APIRequestContext): Promise<void> {
  const venue = await (await request.get(`${AGENT}/venues/ven_fixture`, {
    headers: { "x-draw-internal-key": KEY },
  })).json();
  if (venue?.venue) return;
  const res = await request.post("/api/venues", { data: VENUE_INPUT });
  expect(res.ok(), await res.text()).toBe(true);
}

async function agentRuns(request: APIRequestContext): Promise<number> {
  const all = (await (await request.get(`${AGENT}/__requests`)).json()) as {
    method: string;
    path: string;
  }[];
  return all.filter((r) => r.method === "POST" && r.path === "/runs").length;
}

/** The session cookie's id, so the test can flip its plan on the mock agent. */
async function sessionId(page: Page): Promise<string> {
  const cookie = (await page.context().cookies()).find((c) => c.name === "outdraw_session");
  expect(cookie).toBeTruthy();
  return cookie!.value;
}

test("a free session is refused on the 4th run; a pro session carries on", async ({ page }, info) => {
  // Documentation-range IPs of this spec's own (203.0.113.100-140: errors.spec takes
  // 1-40 and refine.spec 201+): one per request, so only the session's daily count can
  // refuse.
  let n = 0;
  const ip = () => `203.0.113.${100 + n++ + info.retry * 10 + info.workerIndex}`;
  const post = () =>
    page.request.post("/api/runs", { data: RUN, headers: { "x-forwarded-for": ip() } });
  // /api/me counts the IP's hour as well as the plan's day: read from a fresh IP, so the
  // plan's count is the tighter one.
  const me = async () =>
    (await page.request.get("/api/me", { headers: { "x-forwarded-for": ip() } })).json();

  await page.goto("/");
  expect(await me()).toMatchObject({ plan: "free", runs_per_day: 3, remaining_runs: 3 });
  await storeVenue(page.request);

  for (let i = 0; i < 3; i++) {
    const res = await post();
    expect(res.status(), `run ${i + 1}: ${await res.text()}`).toBe(200);
  }
  expect((await me()).remaining_runs).toBe(0);

  const before = await agentRuns(page.request);
  const refused = await post();
  expect(refused.status()).toBe(429);
  expect(await refused.json()).toEqual({
    reason: "plan_limit",
    plan: "free",
    runs_per_day: 3,
    fallback_seed: "nola-jazz",
  });
  expect(Number(refused.headers()["retry-after"])).toBeGreaterThan(0);
  expect(await agentRuns(page.request)).toBe(before);

  // The refine box on a demo page: the same 429, shown with a link to the plans.
  await page.setExtraHTTPHeaders({ "x-forwarded-for": ip() });
  await page.goto("/v/nola-jazz");
  await page.getByRole("button", { name: "More emerging" }).click();
  const alert = page.getByRole("region", { name: "Refine this shortlist" }).getByRole("alert");
  await expect(alert).toHaveAttribute("data-reason", "plan_limit");
  await expect(alert).toHaveText(/You've used the Free plan's 3 runs for today\. Venue Pro gets 30 a day\./);
  await expect(alert.getByRole("link", { name: "See the plans" })).toHaveAttribute("href", "/pricing");
  expect(await agentRuns(page.request)).toBe(before);

  // Flipped to pro (the webhook's move, made directly on the mock): a fresh allowance.
  const id = await sessionId(page);
  const flipped = await page.request.patch(`${AGENT}/sessions/${id}`, {
    data: { plan: "pro" },
    headers: { "x-draw-internal-key": KEY },
  });
  expect(flipped.ok()).toBe(true);
  // 30 left on the plan, 10 on the fresh IP's hour: the IP is the tighter count now.
  expect(await me()).toMatchObject({
    plan: "pro",
    stored_plan: "pro",
    runs_per_day: 30,
    remaining_runs: 10,
  });
  const res = await post();
  expect(res.status()).toBe(200);
  expect(await agentRuns(page.request)).toBe(before + 1);
});

test("the pricing page names the plans and the Stripe roadmap, without a checkout", async ({
  page,
}) => {
  await page.goto("/pricing");
  const plans = page.getByRole("list", { name: "Plans" });
  await expect(plans.getByRole("heading", { name: "Free" })).toBeVisible();
  await expect(plans.getByRole("heading", { name: /Venue Pro/ })).toBeVisible();
  await expect(plans.getByText("$39")).toBeVisible();
  await expect(page.getByText(/Checkout isn't open yet/)).toBeVisible();
  await expect(page.getByText(/free for everyone during the Qloo hackathon/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "What's next" })).toBeVisible();
  await expect(page.getByText(/Nothing is charged today/)).toBeVisible();
  await expect(page.getByRole("link", { name: /Upgrade|Checkout|Subscribe/ })).toHaveCount(0);
});
