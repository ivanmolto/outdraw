// The visual identity (T43): the landing page and a shortlist (the demo venue's page, profile
// and ranked list) as pixel snapshots at a phone width and a desktop width, in both stage
// palettes, plus an axe check against WCAG 2.1 AA on each. Runs in the `visual` project
// (playwright.config.ts: Desktop Chrome at scale 1, so the snapshot is the CSS pixel grid).
//
// Snapshots live in e2e/visual.spec.ts-snapshots, one per platform: text renders differently
// on macOS and Linux, so CI (Linux) compares against the `-linux` files. Regenerate after a
// deliberate change with `npm run test:e2e:snapshots` (local platform) and
// `npm run test:e2e:snapshots:linux` (the Playwright Docker image, for CI).
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const VIEWPORTS = [
  { name: "375", width: 375, height: 812 },
  { name: "1280", width: 1280, height: 800 },
] as const;

const THEMES = ["light", "dark"] as const;

const PAGES = [
  // The landing's seed-fed sections stream in behind skeletons: wait for each.
  { name: "landing", path: "/", ready: "Demo venues", streamed: ["hero-preview", "crowd-signals"] },
  { name: "shortlist", path: "/v/nola-jazz", ready: "Ranked acts", streamed: [] },
] as const;

/** Loads `path` with every font and image in, so a snapshot never catches a swap. */
async function settle(page: Page, path: string, ready: string, streamed: readonly string[]) {
  await page.goto(path);
  // Exact: the demo cards' skeleton is the list "Loading demo venues".
  await expect(page.getByRole("list", { name: ready, exact: true })).toBeVisible();
  for (const id of streamed) await expect(page.getByTestId(id)).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() =>
    Promise.all(
      Array.from(document.images)
        // Only what is drawn: the other theme's lockup is display:none and never loads.
        .filter((img) => !img.complete && img.getClientRects().length > 0)
        .map((img) => new Promise((done) => img.addEventListener("load", done, { once: true }))),
    ),
  );
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    for (const viewport of VIEWPORTS) {
      test.describe(`at ${viewport.name}px`, () => {
        test.use({ viewport: { width: viewport.width, height: viewport.height } });

        for (const target of PAGES) {
          test(`${target.name} looks as intended`, async ({ page }) => {
            await settle(page, target.path, target.ready, target.streamed);
            await expect(page).toHaveScreenshot(
              `${target.name}-${viewport.name}-${theme}.png`,
              { fullPage: true, animations: "disabled" },
            );
          });

          test(`${target.name} passes the axe check`, async ({ page }) => {
            await settle(page, target.path, target.ready, target.streamed);
            const results = await new AxeBuilder({ page })
              .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
              .analyze();
            expect(
              results.violations.map((v) => ({
                id: v.id,
                impact: v.impact,
                nodes: v.nodes.map((n) => n.target),
              })),
            ).toEqual([]);
          });
        }
      });
    }
  });
}

test("the house-lights switch flips the palette and remembers it", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const html = page.locator("html");
  const toggle = page.getByTestId("theme-toggle");
  await expect(toggle).toHaveAttribute("aria-label", "Switch to dark theme");
  await expect(toggle).toHaveAttribute("data-theme-icon", "moon");
  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-label", "Switch to light theme");
  await expect(toggle).toHaveAttribute("data-theme-icon", "sun");
  // The choice survives a reload, applied before paint.
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "light");
});
