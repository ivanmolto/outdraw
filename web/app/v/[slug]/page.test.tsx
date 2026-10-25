// @vitest-environment jsdom
// The seed page against a fake agent: one `GET /seeds/{slug}/page` read, every section
// rendered from it, and the agent's 404 / 409 mapped to the not-found and not-ready pages.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_URL, fakeAgent } from "@/src/server/test-agent";
import type { BriefOut, CompareOut, SeedOut, SeedPageOut } from "@/types/agent";
import venue from "../../venue/[id]/fixtures/venue.json";
import brief from "../../venue/[id]/shortlist/fixtures/brief.json";
import compare from "../../venue/[id]/shortlist/fixtures/compare.json";
import fixture from "../../venue/[id]/shortlist/fixtures/run.json";
import soundcheck from "../../venue/[id]/shortlist/fixtures/soundcheck.json";
import DemoVenuePage from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const HEADLINER = "urn:entity:synthetic:artist:11";
const SECOND = "urn:entity:synthetic:artist:12";

const seed: SeedOut = {
  slug: "nola-jazz",
  title: "Synthetic jazz bar",
  description: "A synthetic room.",
  venue_id: "ven_fixture",
  run_id: "run_fixture",
  venue: venue.venue,
  constraints: fixture.constraints as SeedOut["constraints"],
  message: "",
  ready: true,
  venue_stored: true,
  profile_stored: true,
  run_status: "ok",
  compare_ready: true,
  brief_entity_ids: [HEADLINER],
};
const seedRun = { ...fixture, is_seed: true, seed_slug: "nola-jazz" };
const evidence = (entityId: string) => ({
  run_id: "run_fixture",
  entity_id: entityId,
  score: {},
  evidence: { ...soundcheck.trail, entity_id: entityId },
  pencilled: false,
});

const page = (over: Partial<SeedPageOut> = {}): SeedPageOut => ({
  seed,
  venue: venue as SeedPageOut["venue"],
  run: seedRun as SeedPageOut["run"],
  candidates: [evidence(HEADLINER), evidence(SECOND)],
  calls: [],
  briefs: [{ ...(brief as BriefOut), cached: true }],
  compare: { ...(compare as CompareOut), cached: true },
  ...over,
});

const props = (slug: string) => ({ params: Promise.resolve({ slug }) }) as PageProps<"/v/[slug]">;

describe("/v/[slug]", () => {
  it("renders profile, shortlist and label from the one precomputed read", async () => {
    const sent = fakeAgent(Response.json(page()));
    render(await DemoVenuePage(props("nola-jazz")));

    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([
      `GET ${AGENT_URL}/seeds/nola-jazz/page`,
    ]);
    expect(screen.getByRole("complementary", { name: "About this demo" }).textContent).toContain(
      "Synthetic jazz bar",
    );
    expect(screen.getByTestId("precomputed-label")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Crowd profile" })).toBeTruthy();
    // The shortlist is below: no "Build shortlist", which would start a live run.
    expect(screen.queryByRole("link", { name: "Build shortlist" })).toBeNull();
    expect(screen.getByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to crowd profile" }).getAttribute("href")).toBe(
      "#profile",
    );
  });

  it("renders without a compare or briefs when precompute made none", async () => {
    fakeAgent(Response.json(page({ briefs: [], compare: null })));
    render(await DemoVenuePage(props("nola-jazz")));

    expect(screen.getByRole("list", { name: "Ranked acts" })).toBeTruthy();
  });

  it("says a listed seed is not precomputed yet (the agent's 409)", async () => {
    const sent = fakeAgent(
      Response.json({ detail: { reason: "seed_not_precomputed" } }, { status: 409 }),
    );
    render(await DemoVenuePage(props("nola-jazz")));

    expect(sent).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toContain("This demo isn't ready yet");
  });

  it("is a 404 for an unknown slug, and never asks the agent about a malformed one", async () => {
    fakeAgent(Response.json({ detail: "Unknown seed." }, { status: 404 }));
    await expect(DemoVenuePage(props("no-such-seed"))).rejects.toMatchObject({
      digest: expect.stringContaining("404"),
    });

    const sent = fakeAgent();
    await expect(DemoVenuePage(props("Not A Slug"))).rejects.toMatchObject({
      digest: expect.stringContaining("404"),
    });
    expect(sent).toEqual([]);
  });

  it("maps an agent outage to the user-facing reason", async () => {
    fakeAgent(new TypeError("fetch failed"));
    render(await DemoVenuePage(props("nola-jazz")));

    expect(screen.getByRole("alert").textContent).toContain("Can't reach Outdraw right now");
  });
});
