import { describe, expect, it } from "vitest";

import type { Reason } from "@/types/agent";
import { RETRYABLE, SAMPLED, errorState, reasonText } from "./error-states";

/** Every reason the proxy routes emit (src/server/proxy.ts), and only those. */
const REASONS: Reason[] = [
  "busy",
  "budget",
  "live_mode_off",
  "invalid_input",
  "not_found",
  "conflict",
  "unavailable",
  "unknown",
];

describe("errorState", () => {
  it.each(REASONS)("gives %s a title, a message and a next action", (reason) => {
    const state = errorState(reason);

    expect(state.reason).toBe(reason);
    expect(state.title.length).toBeGreaterThan(0);
    expect(state.message).toBe(reasonText(reason));
    // A next action: a retry, a link, or both.
    expect(state.retryable || state.link !== null).toBe(true);
    expect(state.retryable).toBe(RETRYABLE.has(reason));
  });

  it("sends the refusals of live analysis to the named sample, else the default", () => {
    for (const reason of SAMPLED) {
      expect(errorState(reason, { fallbackSeed: "brooklyn-indie" }).link).toEqual({
        label: "See a sample shortlist",
        href: "/v/brooklyn-indie",
      });
      expect(errorState(reason).link?.href).toBe("/v/nola-jazz");
    }
  });

  it("lists the matches still to confirm for a conflict, and for nothing else", () => {
    const pending = ["venue:Synthetic Room", "Synthetic Act"];

    expect(errorState("conflict", { pending })).toMatchObject({
      pending,
      link: { href: "/venue/new" },
    });
    expect(errorState("unknown", { pending }).pending).toEqual([]);
  });

  it("names the refused field for invalid input, and for nothing else", () => {
    expect(errorState("invalid_input", { field: "past_shows.0.act" }).message).toContain(
      "The field refused was past_shows.0.act.",
    );
    expect(errorState("unknown", { field: "x" }).message).not.toContain("field");
  });

  it("points an outage back at the venue when one is known, else home", () => {
    expect(errorState("unavailable", { venueId: "ven_1" }).link).toEqual({
      label: "Back to crowd profile",
      href: "/venue/ven_1",
    });
    expect(errorState("unknown").link?.href).toBe("/");
    expect(errorState("not_found", { venueId: "ven_1" }).link?.href).toBe("/venue/new");
  });
});
