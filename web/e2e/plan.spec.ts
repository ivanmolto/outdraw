// Sessions and plans (T46) as deployed for judging: HACKATHON_MODE=true (playwright.config.ts)
// shows the banner, makes every anonymous session Pro and leaves every Pro feature open.
import { expect, test } from "@playwright/test";

test("hackathon mode: banner, a Pro session cookie, badges and nothing blocked", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("note")).toHaveText(
    /All Pro features free during the Qloo hackathon/,
  );

  // The first /api/me makes the session and sets its cookie; the id never reaches the body.
  const res = await page.request.get("/api/me");
  expect(res.ok()).toBe(true);
  const me = await res.json();
  expect(me).toMatchObject({ plan: "pro", hackathon: true, runs_per_hour: 10 });
  expect(Object.values(me.features).every((on) => on === true)).toBe(true);
  const cookie = (await page.context().cookies()).find((c) => c.name === "outdraw_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(JSON.stringify(me)).not.toContain(cookie?.value ?? "missing");

  // The same session on the next read.
  expect((await (await page.request.get("/api/me")).json()).stored_plan).toBe("free");
  const again = (await page.context().cookies()).find((c) => c.name === "outdraw_session");
  expect(again?.value).toBe(cookie?.value);

  // A badge where a Pro feature sits, and the feature still open on the demo.
  await page.goto("/venue/new");
  await expect(page.getByText("More than one venue is a Pro feature.")).toBeVisible();
  await page.goto("/v/nola-jazz");
  await page.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }).click();
  const panel = page.getByRole("dialog", { name: "Synthetic Headliner" });
  await panel.getByRole("tab", { name: "Brief" }).click();
  await expect(panel.getByRole("heading", { name: "Outreach email" })).toBeVisible();
});
