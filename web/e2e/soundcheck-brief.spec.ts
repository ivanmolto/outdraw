// The Soundcheck panel's Brief tab and pencil hold against the mock agent (T28): the brief
// is fetched once per act through the real /api proxy, an evidence link lands on the trail
// item it cites, and the pencil hold round-trips through PATCH and survives a reload. The
// venue, profile and run are seeded on the mock directly, as in refine.spec.ts.
import { expect, test, type APIRequestContext } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const KEY = { "x-draw-internal-key": "e2e-internal-key" };
const ACT = "urn:entity:synthetic:artist:11";

interface AgentRequest {
  method: string;
  path: string;
  body: unknown;
}

async function seed(request: APIRequestContext): Promise<string> {
  await request.post(`${AGENT}/venues`, {
    headers: KEY,
    data: {
      name: "Synthetic Room",
      city: "New York",
      country: "US",
      capacity: 150,
      past_shows: [],
    },
  });
  await request.post(`${AGENT}/venues/ven_fixture/profile`, { headers: KEY });
  const run = await request.post(`${AGENT}/runs`, {
    headers: KEY,
    data: {
      venue_id: "ven_fixture",
      constraints: {
        include_tags: [],
        exclude_tags: [],
        size_tier: "emerging",
        exclude_recent_months: 6,
        max_candidates: 40,
      },
      message: "",
    },
  });
  return ((await run.json()) as { run_id: string }).run_id;
}

/** Requests about the headliner the mock agent has received, all specs included. */
async function agentCandidate(request: APIRequestContext): Promise<AgentRequest[]> {
  const all = (await (await request.get(`${AGENT}/__requests`)).json()) as AgentRequest[];
  return all.filter((r) => r.path.includes(`/candidates/${encodeURIComponent(ACT)}`));
}

test("brief tab, evidence link and pencil hold", async ({ page, request }) => {
  const runId = await seed(request);
  const earlier = (await agentCandidate(request)).length;
  await page.goto(`/venue/ven_fixture/shortlist?run=${runId}`);

  await page.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }).click();
  const panel = page.getByRole("dialog", { name: "Synthetic Headliner" });
  await expect(panel.getByRole("heading", { name: "Why this act" })).toBeVisible();
  const pencil = panel.getByRole("switch", { name: "Pencil hold" });
  await expect(pencil).toBeVisible();

  // The brief is fetched when its tab first opens, not with the trail.
  const briefs = async () =>
    (await agentCandidate(request)).slice(earlier).filter((r) => r.path.endsWith("/brief"));
  expect(await briefs()).toHaveLength(0);
  await panel.getByRole("tab", { name: "Brief" }).click();
  for (const field of [
    "Why it fits",
    "Night format",
    "Support act",
    "Partners",
    "Outreach email",
  ]) {
    await expect(panel.getByRole("heading", { name: field })).toBeVisible();
  }
  await expect(panel.getByTestId("fit-reason")).toHaveCount(3);
  await expect(panel.getByTestId("email-subject")).toHaveText(
    "Synthetic Headliner at Synthetic Room",
  );
  await expect(panel.getByRole("button", { name: "Copy subject" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Copy body" })).toBeVisible();
  // The support-pool citation is not in the act's trail: text, not a link.
  await expect(panel.getByTestId("evidence-link")).toHaveCount(2);
  await expect(panel.getByTestId("evidence-text")).toHaveCount(1);
  expect(await briefs()).toHaveLength(1);

  // An evidence link opens the Evidence tab on the cited item, which flashes then settles.
  await panel.getByRole("button", { name: "Evidence: Qloo value 0.81 behind past_fit" }).click();
  await expect(panel.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const cited = panel.locator('[data-ref="affinity:past_fit"]');
  await expect(cited).toBeFocused();
  await expect(cited).toContainText("Past fit 0.81");
  await expect(cited).toHaveAttribute("data-highlight", "");
  await expect(cited).not.toHaveAttribute("data-highlight", "", {
    timeout: 5_000,
  });

  // Back to the brief: the cached copy, no second fetch.
  await panel.getByRole("tab", { name: "Brief" }).click();
  await expect(panel.getByRole("heading", { name: "Outreach email" })).toBeVisible();
  expect(await briefs()).toHaveLength(1);

  // Pencil on: one PATCH, and the hold is there after a reload.
  if ((await pencil.getAttribute("aria-checked")) === "true") {
    await pencil.click();
    await expect(pencil).toHaveAttribute("aria-checked", "false");
  }
  await pencil.click();
  await expect(pencil).toHaveAttribute("aria-checked", "true");
  const patches = async () =>
    (await agentCandidate(request)).slice(earlier).filter((r) => r.method === "PATCH");
  expect((await patches()).at(-1)?.body).toEqual({ pencilled: true });

  await page.reload();
  await page.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }).click();
  await expect(pencil).toHaveAttribute("aria-checked", "true");

  // Pencil off again, so the mock is left as it was found.
  await pencil.click();
  await expect(pencil).toHaveAttribute("aria-checked", "false");
  expect((await patches()).at(-1)?.body).toEqual({ pencilled: false });
});
