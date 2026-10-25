// Seed pages against the mock agent (T30): the landing page's demo cards open /v/{slug},
// which renders the precomputed profile, shortlist, Soundcheck, toggle and brief with no
// request to /api/runs, on load or while browsing, and leaves the Qloo and model counters
// where they were. A refinement forks the seed into a live run on the venue's own page.
//
// What the counters prove: the mock's `/__counters` go up on exactly the routes where the
// real agent spends Qloo calls or model tokens, so a flat count means the web layer never
// hit a spend path. It is not the real agent's Redis counters: that check is
// `npm run check:seeds` against the deployed agent (reads only, and it verifies every seed
// is ready and every trail parses).
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";

interface Counters {
  qloo: number;
  llm: number;
}

const counters = async (request: APIRequestContext): Promise<Counters> =>
  (await (await request.get(`${AGENT}/__counters`)).json()) as Counters;

/** Every request the page sends to our /api routes, from here on. */
function apiRequests(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (url.pathname.startsWith("/api/")) seen.push(`${req.method()} ${url.pathname}`);
  });
  return seen;
}

test("demo cards open seed pages that render every section with no live calls", async ({
  page,
  request,
}) => {
  const before = await counters(request);
  const api = apiRequests(page);

  await page.goto("/");
  const cards = page.getByRole("list", { name: "Demo venues" }).getByRole("link");
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toHaveAttribute("href", "/v/nola-jazz");
  await expect(cards.nth(1)).toHaveAttribute("href", "/v/brooklyn-indie");
  await expect(cards.nth(2)).toHaveAttribute("href", "/v/london-comedy");

  await cards.nth(0).click();
  await expect(page).toHaveURL(/\/v\/nola-jazz$/);

  // Profile, shortlist and toggle, with the demo label.
  await expect(page.getByRole("complementary", { name: "About this demo" })).toContainText(
    "Precomputed demo",
  );
  await expect(page.getByTestId("precomputed-label")).toBeVisible();
  const profile = page.getByRole("region", { name: "Crowd profile" });
  await expect(profile.getByRole("heading", { name: "Genres" })).toBeVisible();
  await expect(profile.getByText("Synthetic Wave")).toBeVisible();
  await expect(profile.getByRole("link", { name: "Build shortlist" })).toHaveCount(0);
  const acts = page.getByRole("list", { name: "Ranked acts" }).getByRole("heading");
  await expect(acts).toHaveText(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);
  await expect(page.getByRole("button", { name: "Refine", exact: true })).toBeVisible();
  expect(api).toEqual([]);

  // Soundcheck and the precomputed brief.
  await page.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }).click();
  const panel = page.getByRole("dialog", { name: "Synthetic Headliner" });
  await expect(panel.getByRole("heading", { name: "Why this act" })).toBeVisible();
  await expect(panel.getByRole("switch", { name: "Pencil hold" })).toHaveCount(0);
  await expect(panel.getByText("Pencil holds are off on the demo.")).toBeVisible();
  await panel.getByRole("tab", { name: "Brief" }).click();
  await expect(panel.getByRole("heading", { name: "Outreach email" })).toBeVisible();
  await panel.getByRole("button", { name: "Close" }).click();

  // An act precompute wrote no brief for says so instead of asking the agent.
  await page.getByRole("button", { name: "Soundcheck for Fixture Collective" }).click();
  const second = page.getByRole("dialog", { name: "Fixture Collective" });
  await second.getByRole("tab", { name: "Brief" }).click();
  await expect(second.getByText("The demo has briefs for its top acts only.")).toBeVisible();
  await second.getByRole("button", { name: "Close" }).click();

  // The LLM-only list (tabs at this viewport).
  await page.getByRole("switch", { name: "With Qloo / LLM only" }).click();
  await page.getByRole("tab", { name: "LLM only" }).click();
  await expect(page.getByRole("list", { name: "LLM-only acts" })).toBeVisible();
  await expect(page.getByTestId("disclaimer")).toBeVisible();

  // A reload is served from the cache: the agent is not read again, and /api/runs stays quiet.
  const pageReads = async () =>
    ((await (await request.get(`${AGENT}/__requests`)).json()) as { path: string }[]).filter(
      (r) => r.path === "/seeds/nola-jazz/page",
    ).length;
  const reads = await pageReads();
  expect(reads).toBe(1);
  await page.reload();
  await expect(acts.first()).toHaveText("Synthetic Headliner");
  expect(await pageReads()).toBe(reads);

  expect(api.filter((r) => r.includes("/api/runs"))).toEqual([]);
  expect(await counters(request)).toEqual(before);
});

test("every seed slug renders, an unknown one is a 404", async ({ page }) => {
  const api = apiRequests(page);
  for (const slug of ["brooklyn-indie", "london-comedy"]) {
    await page.goto(`/v/${slug}`);
    await expect(page.getByRole("list", { name: "Ranked acts" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Crowd profile" })).toBeVisible();
  }
  expect(api).toEqual([]);

  // proxy.ts answers an unknown slug before the page streams, so the status is a real 404.
  const missing = await page.goto("/v/no-such-seed");
  expect(missing?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  expect((await page.goto("/v/Not%20A%20Slug"))?.status()).toBe(404);
  expect(api).toEqual([]);
});

test("a refinement forks the seed into a live run", async ({ page, request }, info) => {
  // Its own rate-limit bucket per attempt: the fork is a counted POST.
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `192.0.2.${info.retry + 101}` });
  const before = await counters(request);
  await page.goto("/v/brooklyn-indie");
  await expect(page.getByTestId("precomputed-label")).toBeVisible();

  const refined = page.waitForRequest(
    (req) =>
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/runs/run_seed_brooklyn-indie/refine",
  );
  await page.getByRole("button", { name: "More emerging" }).click();
  await refined;

  const acts = page.getByRole("list", { name: "Ranked acts" }).getByRole("heading");
  await expect(acts).toHaveText([
    "Fixture Collective",
    "Emerging Fixture Quartet",
    "Synthetic Headliner",
  ]);
  await expect(page).toHaveURL(/\/venue\/ven_seed_brooklyn-indie\/shortlist\?run=run_refined_\d+$/);
  // The live run is no demo: no label, and its own counters spent.
  await expect(page.getByTestId("precomputed-label")).toHaveCount(0);
  const history = page.getByRole("complementary", { name: "Run history" });
  await expect(history.getByRole("button")).toHaveCount(2);
  const after = await counters(request);
  expect(after.qloo).toBeGreaterThan(before.qloo);
  expect(after.llm).toBeGreaterThan(before.llm);

  // The forked URL is a real page: a reload rebuilds the chain from the seed.
  await page.reload();
  await expect(history.getByRole("button")).toHaveCount(2);
  await expect(acts.first()).toHaveText("Fixture Collective");
});
