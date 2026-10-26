// GET /api/seeds/{slug}: a seed's sample from the seed cache, never a run.
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SeedPage } from "@/src/server/seeds";
import type { RunOut } from "@/types/agent";
import venue from "../../../venue/[id]/fixtures/venue.json";
import run from "../../../venue/[id]/shortlist/fixtures/run.json";

const seedPage = vi.fn<(slug: string) => Promise<SeedPage>>();
vi.mock("@/src/server/seeds", () => ({ seedPage: (slug: string) => seedPage(slug) }));

import { AgentError } from "@/src/server/agent";
import { GET } from "./route";

const get = (slug: string) =>
  GET(new Request(`http://web.test/api/seeds/${slug}`), {
    params: Promise.resolve({ slug }),
  } as RouteContext<"/api/seeds/[slug]">);

const precomputed = { runId: "run_seed_nola-jazz", soundcheck: {}, briefs: {}, compare: null };

afterEach(() => seedPage.mockReset());

describe("GET /api/seeds/[slug]", () => {
  it("serves a ready seed's shortlist and its precomputed reads", async () => {
    seedPage.mockResolvedValue({
      state: "ready",
      seed: {
        slug: "nola-jazz",
        title: "Synthetic jazz bar",
        description: "",
        venueName: "Synthetic Room 1",
        city: "New York",
        capacity: 150,
      },
      venueId: "ven_seed_nola-jazz",
      profile: venue.profile as never,
      run: run as RunOut,
      precomputed,
    });

    const res = await get("nola-jazz");

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({
      slug: "nola-jazz",
      title: "Synthetic jazz bar",
      venueId: "ven_seed_nola-jazz",
      venueName: "Synthetic Room 1",
      run: { run_id: run.run_id },
      precomputed,
    });
    expect(Object.keys(body.crossDomain).sort()).toEqual(["brands", "films", "places"]);
    expect(seedPage).toHaveBeenCalledWith("nola-jazz");
  });

  it.each([
    ["missing", 404, "not_found"],
    ["not_ready", 409, "conflict"],
  ] as const)("answers a %s seed with %i", async (state, status, reason) => {
    seedPage.mockResolvedValue(state === "missing" ? { state } : { state, title: "x" });
    const res = await get("brooklyn-indie");
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ reason });
  });

  it("maps a failed read to its reason", async () => {
    seedPage.mockRejectedValue(new AgentError(503, "unavailable"));
    const res = await get("london-comedy");
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ reason: "unavailable" });
  });

  it("is a 404 for a slug that is no seed, without reading anything", async () => {
    const res = await get("not-a-seed");
    expect(res.status).toBe(404);
    expect(seedPage).not.toHaveBeenCalled();
  });
});
