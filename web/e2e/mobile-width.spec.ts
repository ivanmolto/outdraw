// Mobile width (T45): every public page fits a 375px phone with no horizontal scroll, and
// the page links a manifest that makes it installable. Runs in the `mobile` project
// (playwright.config.ts: Pixel 7), narrowed here to the smallest phone width we support.
import { expect, test } from "@playwright/test";

const PAGES = [
  { name: "landing", path: "/", ready: "Demo venues" },
  { name: "venue form", path: "/venue/new", ready: null },
  { name: "seed shortlist", path: "/v/nola-jazz", ready: "Ranked acts" },
  { name: "seed shortlist (second)", path: "/v/brooklyn-indie", ready: "Ranked acts" },
  { name: "seed shortlist (third)", path: "/v/london-comedy", ready: "Ranked acts" },
] as const;

test.use({ viewport: { width: 375, height: 812 } });

for (const { name, path, ready } of PAGES) {
  test(`${name} has no horizontal scroll at 375px`, async ({ page }) => {
    await page.goto(path);
    if (ready) await expect(page.getByRole("list", { name: ready, exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const widths = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(widths.document, `document.scrollWidth on ${path}`).toBeLessThanOrEqual(widths.viewport);
    expect(widths.body, `body.scrollWidth on ${path}`).toBeLessThanOrEqual(widths.viewport);
  });
}

test("the page links an installable manifest", async ({ page, request }) => {
  await page.goto("/");
  const href = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(href).toBeTruthy();
  const res = await request.get(href as string);
  expect(res.ok()).toBe(true);
  const manifest = (await res.json()) as {
    name: string;
    start_url: string;
    display: string;
    icons: { src: string; sizes: string; purpose?: string }[];
  };
  expect(manifest.name).toBe("Outdraw");
  expect(manifest.start_url).toBe("/");
  expect(manifest.display).toBe("standalone");
  expect(manifest.icons.map((i) => i.sizes)).toEqual(
    expect.arrayContaining(["192x192", "512x512"]),
  );
  expect(manifest.icons.some((i) => i.purpose === "maskable")).toBe(true);
  for (const icon of manifest.icons) expect((await request.get(icon.src)).ok()).toBe(true);
});
