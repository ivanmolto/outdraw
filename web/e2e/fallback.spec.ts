// The kill switch and the daily budget as the site sees them (T33): the mock agent refuses
// every call that would spend as the real one does (`POST /__refuse`: 503 live_mode_off, 429
// budget, both naming brooklyn-indie, the mock's Synthetic Room 2). A refused run shows that
// seed's precomputed sample under a banner; a refused match, profile or brief says it waits
// for live analysis and links the sample. Never an error, a stack trace or a call spent. The web's own rate limit (429 busy) is in ratelimit.spec.ts. Each test sets its own
// x-forwarded-for, so the per-IP limits never carry over.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const VENUE_ID = "ven_fixture";
const BANNER = "Live analysis is busy, here's a sample";
const SAMPLE_VENUE = "Synthetic Room 2";
const LIVE_VENUE = "Synthetic Room";

/** A documentation-range IP unique to this test attempt (other specs use 192.0.2.1-102). */
function ipFor(info: { workerIndex: number; retry: number }, base: number): string {
  return `192.0.2.${base + info.retry * 10 + info.workerIndex}`;
}

const refuse = (request: APIRequestContext, status: 429 | 503 | null) =>
  request.post(`${AGENT}/__refuse`, { data: { status } });

async function counters(request: APIRequestContext): Promise<{ qloo: number; llm: number }> {
  return (await request.get(`${AGENT}/__counters`)).json();
}

/** The live venue with a profile, as the visitor `ip` (the profile build is one run). */
async function profiledVenue(page: Page, request: APIRequestContext, ip: string): Promise<void> {
  const headers = { "x-forwarded-for": ip };
  await request.post("/api/venues", {
    data: { name: LIVE_VENUE, city: "New York", country: "US", capacity: 150, past_shows: [] },
    headers,
  });
  expect((await request.post(`/api/venues/${VENUE_ID}/profile`, { headers })).status()).toBe(200);
  await page.setExtraHTTPHeaders(headers);
}

/** The sample is on screen and nothing reads as an error. */
async function expectSample(page: Page): Promise<void> {
  await expect(page.getByRole("status", { name: "Sample shortlist" })).toContainText(BANNER);
  await expect(page.getByRole("heading", { name: SAMPLE_VENUE, exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Ranked acts" })).toBeVisible();
  // In <main>: Next's route announcer is a role="alert" of its own.
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  for (const text of ["Internal Server Error", "Couldn't", "switched off", "budget", "8787"]) {
    await expect(page.getByText(text)).toHaveCount(0);
  }
  await expect(page.getByRole("link", { name: "Open the full demo" })).toHaveAttribute(
    "href",
    "/v/brooklyn-indie",
  );
}

test.afterEach(async ({ request }) => {
  await refuse(request, null);
});

for (const [status, reason] of [
  [503, "live_mode_off"],
  [429, "budget"],
] as const) {
  test(`a ${status} ${reason} run shows the named seed's sample`, async ({ page, request }, info) => {
    await profiledVenue(page, request, ipFor(info, status === 503 ? 151 : 171));
    await refuse(request, status);
    const spent = await counters(request);

    const refused = page.waitForResponse((r) => r.url().endsWith("/api/runs"));
    await page.goto(`/venue/${VENUE_ID}/shortlist`);

    const res = await refused;
    expect(res.status()).toBe(status);
    expect(await res.json()).toEqual({ reason, fallback_seed: "brooklyn-indie" });
    await expectSample(page);
    expect(page.url()).not.toContain("?run=");
    expect(await counters(request)).toEqual(spent);

    // Live again: the retry runs the venue's own shortlist.
    await refuse(request, null);
    await page.getByRole("button", { name: "Try a live run again" }).click();
    await expect(page.getByRole("heading", { name: LIVE_VENUE, exact: true })).toBeVisible();
    await expect(page.getByRole("status", { name: "Sample shortlist" })).toHaveCount(0);
  });
}

test("a refused refinement swaps in the sample, and goes back", async ({ page, request }, info) => {
  await profiledVenue(page, request, ipFor(info, 191));
  await page.goto(`/venue/${VENUE_ID}/shortlist`);
  await expect(page.getByRole("heading", { name: LIVE_VENUE, exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\?run=/);
  const url = page.url();

  await refuse(request, 503);
  const refused = page.waitForResponse((r) => r.url().endsWith("/refine"));
  await page.getByRole("button", { name: "Bigger acts" }).click();

  expect((await refused).status()).toBe(503);
  await expectSample(page);
  await expect(page.getByText("Refine this shortlist")).toHaveCount(0);
  expect(page.url()).toBe(url);

  await page.getByRole("button", { name: "Back to your shortlist" }).click();
  await expect(page.getByRole("heading", { name: LIVE_VENUE, exact: true })).toBeVisible();
  await expect(page.getByText("Refine this shortlist")).toBeVisible();
});

test("matching and the profile wait for live analysis, with no error", async ({ page, request }, info) => {
  await page.setExtraHTTPHeaders({ "x-forwarded-for": ipFor(info, 211) });
  await refuse(request, 503);
  const spent = await counters(request);
  await page.goto("/venue/new");

  await page.getByLabel("Venue name", { exact: true }).fill(LIVE_VENUE);
  await page.getByLabel("City", { exact: true }).fill("New York");
  await page.getByLabel("Country", { exact: true }).fill("US");
  await page.getByLabel("Capacity", { exact: true }).fill("150");
  await page.getByRole("button", { name: "Add a past show" }).click();
  await page.getByLabel("Act", { exact: true }).fill("Synthetic Act");
  await page.getByLabel("Date", { exact: true }).fill("2026-06-12");
  await page.getByRole("button", { name: "Save and match acts" }).click();

  const paused = page.getByRole("status", { name: "Live analysis paused" });
  await expect(paused).toContainText("Your venue is saved");
  await expect(paused.getByRole("link", { name: "See a sample shortlist" })).toHaveAttribute(
    "href",
    "/v/brooklyn-indie",
  );
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);

  // Matching once live analysis is back, then the profile refused in turn.
  await refuse(request, null);
  await page.getByRole("button", { name: "Save and match acts" }).click();
  await expect(page.getByRole("heading", { name: "Confirm matches" })).toBeVisible();
  const matched = await counters(request);
  await refuse(request, 429);
  await page.getByRole("button", { name: "Build crowd profile" }).click();
  await expect(paused).toContainText("crowd profile can't be built right now");
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await counters(request)).toEqual(matched);
  expect(matched.qloo - spent.qloo).toBe(1);
});

test("a first brief waits for live analysis; the panel still opens", async ({ page, request }, info) => {
  await profiledVenue(page, request, ipFor(info, 231));
  await page.goto(`/venue/${VENUE_ID}/shortlist`);
  await expect(page.getByRole("heading", { name: LIVE_VENUE, exact: true })).toBeVisible();
  await refuse(request, 503);
  const spent = await counters(request);

  await page.getByRole("button", { name: "Soundcheck for Fixture Collective" }).click();
  const panel = page.getByRole("dialog", { name: "Fixture Collective" });
  await expect(panel.getByRole("heading", { name: "Why this act" })).toBeVisible();
  await panel.getByRole("tab", { name: "Brief" }).click();
  await expect(panel.getByRole("status", { name: "Live analysis paused" })).toContainText(
    "booking brief can't be drafted right now",
  );
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(await counters(request)).toEqual(spent);
});

test("the LLM-only list waits for live analysis; Outdraw's list stays", async ({ page, request }, info) => {
  await profiledVenue(page, request, ipFor(info, 251));
  await page.goto(`/venue/${VENUE_ID}/shortlist`);
  await expect(page.getByRole("heading", { name: LIVE_VENUE, exact: true })).toBeVisible();
  await refuse(request, 429);
  const spent = await counters(request);

  // Tabs at this viewport: the LLM-only panel is hidden until its tab is picked.
  await page.getByRole("switch", { name: "With Qloo / LLM only" }).click();
  await page.getByRole("tab", { name: "LLM only" }).click();
  await expect(page.getByRole("status", { name: "Live analysis paused" })).toContainText(
    "LLM-only list can't be fetched right now",
  );
  await page.getByRole("tab", { name: "With Qloo" }).click();
  await expect(page.getByRole("list", { name: "Ranked acts" })).toBeVisible();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await counters(request)).toEqual(spent);
});
