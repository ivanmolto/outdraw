// Refinement against the mock agent: chips and the message box chain new runs through the
// real /api/runs/{id}/refine proxy, each rendered with its diff and listed in the history.
// The venue, profile and first run are seeded on the mock directly, so this spec does not
// depend on the venue flow having run first and spends its rate-limit burst on refinements.
import { expect, test, type APIRequestContext } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const KEY = { "x-draw-internal-key": "e2e-internal-key" };

async function seed(request: APIRequestContext): Promise<string> {
  await request.post(`${AGENT}/venues`, {
    headers: KEY,
    data: { name: "Synthetic Room", city: "New York", country: "US", capacity: 150, past_shows: [] },
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

/** POST /runs/{id}/refine the mock agent has received, all specs included. */
async function agentRefines(request: APIRequestContext) {
  const all = (await (await request.get(`${AGENT}/__requests`)).json()) as {
    method: string;
    path: string;
    body: unknown;
  }[];
  return all.filter((r) => r.method === "POST" && r.path.endsWith("/refine"));
}

test("chip click updates the list, and three refinements chain with diffs", async ({
  page,
  request,
}, info) => {
  // Its own rate-limit bucket per attempt: three refinements fill the 3-a-minute burst.
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `203.0.113.${info.retry + 1}` });
  const runId = await seed(request);
  const earlier = (await agentRefines(request)).length;
  await page.goto(`/venue/ven_fixture/shortlist?run=${runId}`);

  const acts = page.getByRole("list", { name: "Ranked acts" }).getByRole("heading");
  await expect(acts).toHaveText(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);
  const history = page.getByRole("complementary", { name: "Run history" });
  await expect(history).toHaveCount(0);

  // 1: a chip.
  await page.getByRole("button", { name: "More emerging" }).click();
  await expect(acts).toHaveText([
    "Fixture Collective",
    "Emerging Fixture Quartet",
    "Synthetic Headliner",
  ]);
  const entered = page.getByRole("listitem").filter({ hasText: "Emerging Fixture Quartet" });
  await expect(entered.getByTestId("change")).toHaveText("New");
  await expect(
    page.getByRole("region", { name: "Left the list" }).getByText("Test Pattern Trio"),
  ).toBeVisible();
  await expect(page).toHaveURL(/\?run=run_refined_\d+$/);
  await expect(history.getByRole("button")).toHaveCount(2);
  await expect(history.getByText("“More emerging acts”")).toBeVisible();

  // 2: another chip, chained to the refined run.
  await page.getByRole("button", { name: "Exclude tributes" }).click();
  await expect(history.getByRole("button")).toHaveCount(3);
  await expect(acts).toHaveText([
    "Emerging Fixture Quartet",
    "Synthetic Headliner",
    "Fixture Collective",
  ]);
  await expect(page.getByTestId("change")).toHaveCount(3);

  // 3: the booker's own words.
  await page.getByLabel("Or say what to change").fill("Cap it at 300");
  await page.getByRole("button", { name: "Refine", exact: true }).click();
  await expect(history.getByRole("button")).toHaveCount(4);
  await expect(history.getByText("“Cap it at 300”")).toBeVisible();
  await expect(acts).toHaveText([
    "Synthetic Headliner",
    "Fixture Collective",
    "Emerging Fixture Quartet",
  ]);

  const refines = (await agentRefines(request)).slice(earlier);
  expect(refines.map((r) => r.body)).toEqual([
    { message: "More emerging acts" },
    { message: "No tribute acts" },
    { message: "Cap it at 300" },
  ]);
  // Each refinement chains to the run before it.
  expect(refines[0].path).toBe(`/runs/${runId}/refine`);
  expect(refines[1].path).not.toBe(refines[0].path);
  expect(refines[2].path).not.toBe(refines[1].path);

  // A reload rebuilds the chain from parent_run_id.
  await page.reload();
  await expect(history.getByRole("button")).toHaveCount(4);
  await expect(acts.first()).toHaveText("Synthetic Headliner");
});
