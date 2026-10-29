// Answering an ask (review T51): a run that stopped at an ambiguous tag word shows the
// Qloo tags as choices; a click posts the choice through the real /api/runs/{id}/answer
// proxy and the run it starts is rendered with its history entry. The venue, profile and
// the ask run are seeded on the mock directly (after errors.spec, which needs the venue
// absent). Its own IP base, 198.51.100.150+: ratelimit.spec spends 1-110 in this range
// and plan-free.spec 200+, and a shared IP would hand this spec their spent burst.
import { expect, test, type APIRequestContext } from "@playwright/test";

const AGENT = "http://127.0.0.1:8787";
const KEY = { "x-draw-internal-key": "e2e-internal-key" };
const VENUE_ID = "ven_fixture";

async function seedAsk(request: APIRequestContext): Promise<string> {
  await request.post(`${AGENT}/venues`, {
    headers: KEY,
    data: { name: "Synthetic Room", city: "New York", country: "US", capacity: 150, past_shows: [] },
  });
  await request.post(`${AGENT}/venues/${VENUE_ID}/profile`, { headers: KEY });
  const run = await request.post(`${AGENT}/runs`, {
    headers: KEY,
    data: {
      venue_id: VENUE_ID,
      constraints: {
        include_tags: ["ambiguous"],
        exclude_tags: [],
        size_tier: "mid",
        exclude_recent_months: 6,
        max_candidates: 40,
      },
      message: "",
    },
  });
  const body = (await run.json()) as { run_id: string; status: string };
  expect(body.status).toBe("ask");
  return body.run_id;
}

test("a tag ask is answered with a click and the new run takes its place", async ({
  page,
  request,
}, info) => {
  const askId = await seedAsk(request);
  await page.setExtraHTTPHeaders({
    "x-forwarded-for": `198.51.100.${150 + info.retry * 10 + info.workerIndex}`,
  });
  await page.goto(`/venue/${VENUE_ID}/shortlist?run=${askId}`);

  const alert = page.getByRole("main").getByRole("alert");
  await expect(alert.getByRole("heading", { name: "Outdraw needs a choice first" })).toBeVisible();
  await expect(alert.getByText("Which Qloo tag is meant for 'ambiguous'?")).toBeVisible();
  const choices = alert.getByRole("list", { name: "Choices" }).getByRole("button");
  await expect(choices).toHaveText(["Ambiguous Rock for ambiguous", "Ambiguous Pop for ambiguous"]);

  const answered = page.waitForResponse((r) => r.url().includes("/answer"));
  await choices.nth(1).click();
  const reply = await answered;
  expect(reply.status(), await reply.text()).toBe(200);

  await expect(page.getByRole("list", { name: "Ranked acts" })).toBeVisible();
  await expect(
    page.getByText("Searched with the Qloo tag 'Ambiguous Pop' you chose for 'ambiguous'."),
  ).toBeVisible();
  await expect(page).toHaveURL(/\?run=run_refined_\d+$/);
  const history = page.getByRole("complementary", { name: "Run history" });
  await expect(history.getByRole("button")).toHaveCount(2);
  await expect(history).toContainText("Use 'Ambiguous Pop' for 'ambiguous'");

  const sent = (await (await request.get(`${AGENT}/__requests`)).json()) as {
    method: string;
    path: string;
    body: { for_input?: string; entity_id?: string };
  }[];
  const answers = sent.filter((r) => r.path === `/runs/${askId}/answer`);
  expect(answers.map((r) => r.body)).toEqual([
    { for_input: "ambiguous", entity_id: "urn:tag:genre:music:ambiguous_pop" },
  ]);
});
