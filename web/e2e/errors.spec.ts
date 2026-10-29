// Friendly error states (T40) against a broken agent: with the mock crashing (a bare 500
// whose body carries a traceback and its own URL) or unreachable, every page shows a clear
// state with a next action, the /api routes answer `{reason}` alone, and nothing of the
// backend's (its status text, its trace, its host) reaches the browser. Runs first in the
// suite (file order), before any seed page is cached, so the demo page is read live here.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const VENUE_ID = "ven_fixture";

/** What the mock's crash body carries, and what the real agent's host would: none of it may
 * show up in a page or an /api response. */
const LEAK = /Internal Server Error|Traceback|synthetic crash|routes\.py|8787|127\.0\.0\.1/;

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

const fail = (request: APIRequestContext, mode: "crash" | "down" | "qloo" | null) =>
  request.post(`${AGENT}/__fail`, { data: { mode } });

/** A documentation-range IP unique to this test attempt (other specs use 192.0.2.1-200). */
function ipFor(info: { workerIndex: number; retry: number }, base: number): string {
  return `203.0.113.${base + info.retry * 10 + info.workerIndex}`;
}

/** The page's one error state, settled, with nothing of the backend's anywhere in the
 * document. */
async function expectState(page: Page, reason: string): Promise<void> {
  const alert = page.getByRole("main").getByRole("alert");
  await expect(alert).toHaveAttribute("data-reason", reason);
  await expect(alert.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(alert.getByRole("link", { name: "Try again" })).toBeVisible();
  expect(await page.content()).not.toMatch(LEAK);
}

test.afterEach(async ({ request }) => {
  await fail(request, null);
});

test("a crashing agent: the API answers {reason} alone and every page a friendly state", async ({
  page,
  request,
}, info) => {
  await fail(request, "crash");
  const headers = { "x-forwarded-for": ipFor(info, 1) };

  for (const [path, data] of [
    ["/api/runs", RUN],
    ["/api/venues", { name: "Crash Room", city: "New York", country: "US", capacity: 150, past_shows: [] }],
  ] as const) {
    const res = await request.post(path, { data, headers });
    expect(res.status(), path).toBe(502);
    expect(await res.json(), path).toEqual({ reason: "unknown" });
    expect(await res.text(), path).not.toMatch(LEAK);
  }

  await page.setExtraHTTPHeaders(headers);
  for (const path of [`/venue/${VENUE_ID}`, `/venue/${VENUE_ID}/shortlist`, "/v/nola-jazz"]) {
    await page.goto(path);
    await expectState(page, "unknown");
    await expect(page.getByText("Something went wrong on our side")).toBeVisible();
  }

  // The landing page keeps its way in, and says the demos are off.
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Set up your venue" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("demo venues aren't available");
  expect(await page.content()).not.toMatch(LEAK);
});

test("a refused Qloo key: the API names qloo_error with its code, the page says it is on our side", async ({
  page,
  request,
}, info) => {
  await fail(request, "qloo");
  const headers = { "x-forwarded-for": ipFor(info, 261) };

  const res = await request.post("/api/runs", { data: RUN, headers });
  expect(res.status()).toBe(502);
  expect(await res.json()).toEqual({ reason: "qloo_error", code: "QLOO_AUTH", retryable: false });

  await page.setExtraHTTPHeaders(headers);
  await page.goto(`/venue/${VENUE_ID}`);
  const alert = page.getByRole("main").getByRole("alert");
  await expect(alert).toHaveAttribute("data-reason", "qloo_error");
  await expect(alert.getByText("This is on our side")).toBeVisible();
  // Not retryable: no "Try again"; a demo stands in instead.
  await expect(alert.getByRole("link", { name: "Try again" })).toHaveCount(0);
  await expect(alert.getByRole("link", { name: "See a sample shortlist" })).toBeVisible();
  // The code travels as data (the API body carries it too); the harness's recovery text,
  // which names a config path, must not.
  const content = await page.content();
  expect(content).not.toMatch(LEAK);
  expect(content).not.toMatch(/\.qloo\/config|qloo setup/);
  await expect(alert).not.toContainText("QLOO_AUTH");
});

test("an unreachable agent: pages say Outdraw can't be reached, and the retry reloads", async ({
  page,
  request,
}, info) => {
  await fail(request, "down");
  const headers = { "x-forwarded-for": ipFor(info, 21) };

  const res = await request.post("/api/runs", { data: RUN, headers });
  expect(res.status()).toBe(503);
  expect(await res.json()).toEqual({ reason: "unavailable" });

  await page.setExtraHTTPHeaders(headers);
  await page.goto(`/venue/${VENUE_ID}`);
  await expectState(page, "unavailable");
  await expect(page.getByText("Can't reach Outdraw right now")).toBeVisible();
  await expect(page.getByRole("link", { name: "Try again" })).toHaveAttribute(
    "href",
    `/venue/${VENUE_ID}`,
  );

  // Another seed than the crash test's: a failed read is kept for a moment (seeds.ts).
  await page.goto("/v/brooklyn-indie");
  await expectState(page, "unavailable");

  // Back up: "Try again" is a full reload of the venue page, and the venue (not posted
  // yet) is then a real 404.
  await page.goto(`/venue/${VENUE_ID}`);
  await expectState(page, "unavailable");
  await fail(request, null);
  await page.getByRole("link", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Venue not found" })).toBeVisible();
  expect(await page.content()).not.toMatch(LEAK);
});
