// Hackathon mode off (T46), on the `free` project's server: no banner, a new session is
// free, and each Pro feature gives way to its note: the export, the brief and the run
// history here;
// the mock's runs have three acts, so the shortlist gate past the top 5 is covered by the
// component tests and `full_shortlist` through /api/me's feature map.
import { expect, test } from "@playwright/test";

test("free plan: no banner, free session, Pro features give way to their notes", async ({
  page,
}, info) => {
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `198.51.100.${200 + info.retry}` });
  await page.goto("/");
  await expect(page.getByRole("note")).toHaveCount(0);

  const me = await (await page.request.get("/api/me")).json();
  expect(me).toMatchObject({ plan: "free", stored_plan: "free", hackathon: false });
  expect(Object.values(me.features).every((on) => on === false)).toBe(true);

  // The export: the note stands where the menu would.
  await page.goto("/v/nola-jazz");
  await expect(page.getByRole("note").filter({ hasText: "Exports" })).toHaveText(
    /Exports to your booking tool: a Pro feature/,
  );
  await expect(page.getByText("Export to my booking tool")).toHaveCount(0);

  // The brief: tab still there, badged; its panel is the note.
  await page.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }).click();
  const panel = page.getByRole("dialog", { name: "Synthetic Headliner" });
  await panel.getByRole("tab", { name: "Brief" }).click();
  await expect(panel.getByRole("note")).toHaveText(/Booking briefs and outreach: a Pro feature/);
  await expect(panel.getByRole("heading", { name: "Outreach email" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Close" }).click();

  // Run history: a refinement forks the seed into a chain of two; the sidebar is the note.
  await page.getByRole("button", { name: "More emerging" }).click();
  await expect(page).toHaveURL(/\/venue\/.*\/shortlist\?run=/);
  await expect(page.getByRole("note").filter({ hasText: "Run history" })).toHaveText(
    /Run history: a Pro feature/,
  );
  await expect(page.getByRole("complementary", { name: "Run history" })).toHaveCount(0);
});
