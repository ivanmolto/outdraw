import { describe, expect, it } from "vitest";

import type { Reason } from "@/types/agent";
import { RETRYABLE, SAMPLED, busyNote, errorState, reasonText } from "./error-states";

/** Every reason the proxy routes emit (src/server/proxy.ts), and only those. */
const REASONS: Reason[] = [
  "busy",
  "budget",
  "live_mode_off",
  "plan_limit",
  "invalid_input",
  "not_found",
  "conflict",
  "qloo_error",
  "unavailable",
  "unknown",
];

describe("qloo_error", () => {
  it("names our own setup for a key or harness failure and sends to a demo, no retry", () => {
    const state = errorState("qloo_error", { code: "QLOO_AUTH", retryable: false, venueId: "ven_1" });
    expect(state.message).toContain("This is on our side");
    expect(state.retryable).toBe(false);
    expect(state.link?.href).toMatch(/^\/v\//);
  });

  it("offers a retry only when the body says the call can succeed later", () => {
    const later = errorState("qloo_error", { code: "harness_timeout", retryable: true, venueId: "ven_1" });
    expect(later.message).toContain("Try again in a moment");
    expect(later.retryable).toBe(true);
    expect(later.link).toEqual({ label: "Back to crowd profile", href: "/venue/ven_1" });

    const plain = errorState("qloo_error", { code: "QLOO_HTTP", retryable: false });
    expect(plain.message).toBe(reasonText("qloo_error"));
    expect(plain.retryable).toBe(false);
    expect(plain.link).toEqual({ label: "Back to the start", href: "/" });
  });

  it("never shows the code or Qloo's recovery text", () => {
    const state = errorState("qloo_error", { code: "harness_failed", retryable: false });
    expect(state.message).not.toContain("harness_failed");
    expect(state.title).not.toContain("harness_failed");
  });
});

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

  it("names the plan that ran out of runs: free sees the plans, pro sees a sample", () => {
    const free = errorState("plan_limit", { plan: "free", runsPerDay: 3 });
    expect(free.message).toBe("You've used the Free plan's 3 runs for today. Venue Pro gets 30 a day.");
    expect(free.link).toEqual({ label: "See the plans", href: "/pricing" });
    expect(free.retryable).toBe(false);

    const pro = errorState("plan_limit", { plan: "pro", runsPerDay: 30, fallbackSeed: "brooklyn-indie" });
    expect(pro.message).toBe("You've used the Pro plan's 30 runs for today. Try again tomorrow.");
    expect(pro.link).toEqual({ label: "See a sample shortlist", href: "/v/brooklyn-indie" });

    // Without the details (an older body): the plain message, and the plans to look at.
    expect(errorState("plan_limit")).toMatchObject({
      message: reasonText("plan_limit"),
      link: { href: "/pricing" },
    });
  });

  it("says which IP window tripped and the real wait for a busy off the rate limit", () => {
    const hourly = errorState("busy", { limit: "hourly", runs: 10, retryAfter: 700, fallbackSeed: "brooklyn-indie" });
    expect(hourly.message).toBe(
      "You've reached 10 live runs this hour. Try again in 12 minutes, or open a demo venue.",
    );
    expect(hourly.link).toEqual({ label: "See a sample shortlist", href: "/v/brooklyn-indie" });
    expect(hourly.retryable).toBe(true);

    expect(errorState("busy", { limit: "hourly", runs: 10, retryAfter: 60 }).message).toContain(
      "Try again in 1 minute,",
    );
    expect(errorState("busy", { limit: "burst", runs: 3, retryAfter: 40 }).message).toBe(
      "That's 3 live runs inside a minute. Try again in 40 seconds.",
    );
    expect(errorState("busy", { limit: "burst", runs: 3, retryAfter: 1 }).message).toContain(
      "in 1 second.",
    );
    // The wait rounds up, never down: 61 seconds is not "1 minute".
    expect(busyNote({ limit: "hourly", runs: 10, retryAfter: 61 })).toContain("2 minutes");

    // An agent-side refusal, or an older body, names no window: the plain message stands.
    expect(errorState("busy").message).toBe(reasonText("busy"));
    expect(errorState("busy", { fallbackSeed: "nola-jazz" }).message).toBe(reasonText("busy"));
    expect(busyNote({ limit: "hourly", runs: 10 })).toBeNull();
    expect(busyNote({ retryAfter: 30 })).toBeNull();
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
