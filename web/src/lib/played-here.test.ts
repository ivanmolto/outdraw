import { describe, expect, it } from "vitest";

import type { VenueOut } from "@/types/agent";
import { normaliseName, playedHere } from "./played-here";

type Item = VenueOut["resolutions"][number];

const act = (query: string, entityId: string | null, over: Partial<Item> = {}): Item => ({
  kind: "act",
  query,
  confirmed: true,
  entity_id: entityId,
  canonical_name: query,
  candidates: [],
  ...over,
});

const venue = (
  past_shows: VenueOut["venue"]["past_shows"],
  resolutions: Item[],
): Pick<VenueOut, "venue" | "resolutions"> => ({
  venue: { name: "Synthetic Room", city: "New York", country: "US", capacity: 400, past_shows },
  resolutions,
});

describe("playedHere", () => {
  it("keys each confirmed act's latest show by entity, with its sell-through", () => {
    const out = playedHere(
      venue(
        [
          { act: "Synthetic Act", date: "2025-11-02", tickets_sold: 100 },
          { act: "synthetic act!", date: "2026-03-14", tickets_sold: 380, capacity: 400 },
          { act: "Fixture Duo", date: "2026-01-10" },
        ],
        [act("Synthetic Act", "E1"), act("Fixture Duo", "E2")],
      ),
    );

    expect(out).toEqual({
      E1: { date: "2026-03-14", sellThrough: 0.95 },
      E2: { date: "2026-01-10", sellThrough: null },
    });
  });

  it("falls back to the venue's capacity and matches the canonical name too", () => {
    const out = playedHere(
      venue(
        [{ act: "The Synthetic Band", date: "2026-02-01", tickets_sold: 200 }],
        [act("synth band", "E1", { canonical_name: "Synthetic Band" })],
      ),
    );

    expect(out.E1).toEqual({ date: "2026-02-01", sellThrough: 0.5 });
  });

  it("leaves out unconfirmed, unmatched and venue resolutions", () => {
    const out = playedHere(
      venue(
        [{ act: "Synthetic Act", date: "2026-03-14" }],
        [
          act("Synthetic Act", "E1", { confirmed: false }),
          act("Synthetic Act", null),
          act("Synthetic Act", "E3", { kind: "venue" }),
          act("Someone Else", "E4"),
        ],
      ),
    );

    expect(out).toEqual({});
  });
});

describe("normaliseName", () => {
  it("matches the agent's normalise_name", () => {
    expect(normaliseName("The Beyoncé & Co.")).toBe("beyonceandco");
    expect(normaliseName("  Synthetic   Act ")).toBe("syntheticact");
  });
});
