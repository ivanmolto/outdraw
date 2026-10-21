// The venue flow end to end against the mock agent: form → Qloo match → crowd profile →
// shortlist, through the real /api proxy and server components.
import { expect, test } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";

test("form → profile → shortlist", async ({ page, request }) => {
  await page.goto("/venue/new");

  await page.getByLabel("Venue name", { exact: true }).fill("Synthetic Room");
  await page.getByLabel("City", { exact: true }).fill("New York");
  await page.getByLabel("Country", { exact: true }).fill("US");
  await page.getByLabel("Capacity", { exact: true }).fill("150");
  await page.getByRole("button", { name: "Add a past show" }).click();
  await page.getByLabel("Act", { exact: true }).fill("Synthetic Act");
  await page.getByLabel("Date", { exact: true }).fill("2026-06-12");
  await page.getByRole("button", { name: "Save and match acts" }).click();

  await expect(page.getByRole("heading", { name: "Confirm matches" })).toBeVisible();
  await page.getByRole("button", { name: "Build crowd profile" }).click();

  await expect(page).toHaveURL(/\/venue\/ven_fixture$/);
  await expect(page.getByRole("heading", { level: 1, name: "Synthetic Room" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Genres" })).toBeVisible();
  await page.getByRole("link", { name: "Build shortlist" }).click();

  await expect(page.getByRole("status", { name: "Building shortlist" })).toBeVisible();
  const acts = page.getByRole("list", { name: "Ranked acts" });
  await expect(acts).toBeVisible();
  await expect(acts.getByRole("heading")).toHaveText([
    "Synthetic Headliner",
    "Fixture Collective",
    "Test Pattern Trio",
  ]);
  await expect(page.getByTestId("total").first()).toHaveText("Score 0.81");
  await expect(page.getByText(/recommend puts Synthetic Headliner at 92% affinity/)).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Sub-scores for Synthetic Headliner" }).getByTestId("chip"),
  ).toHaveCount(6);
  await expect(page).toHaveURL(/\/venue\/ven_fixture\/shortlist\?run=run_fixture$/);

  const runs = (await (await request.get(`${AGENT}/__requests`)).json()).filter(
    (r: { method: string; path: string }) => r.method === "POST" && r.path === "/runs",
  );
  expect(runs).toHaveLength(1);
  expect(runs[0].body).toMatchObject({
    venue_id: "ven_fixture",
    constraints: { size_tier: "emerging", include_tags: [], exclude_tags: [] },
  });

  // A reload reads the stored run instead of spending Qloo calls on a new one.
  await page.reload();
  await expect(acts.getByRole("heading").first()).toHaveText("Synthetic Headliner");
  const after = (await (await request.get(`${AGENT}/__requests`)).json()).filter(
    (r: { method: string; path: string }) => r.method === "POST" && r.path === "/runs",
  );
  expect(after).toHaveLength(1);
});
