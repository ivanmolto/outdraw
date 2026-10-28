import { describe, expect, it } from "vitest";

import {
  effectivePlan,
  entitlements,
  hackathonMode,
  me,
  PRO_FEATURES,
  shows,
  type ProFeature,
} from "./entitlements";

const FEATURES = Object.keys(PRO_FEATURES) as ProFeature[];

describe("hackathon mode", () => {
  it("is on only for the word true", () => {
    expect(hackathonMode("true")).toBe(true);
    expect(hackathonMode(" TRUE ")).toBe(true);
    for (const off of [undefined, "", "false", "1", "yes", "on"]) {
      expect(hackathonMode(off)).toBe(false);
    }
  });

  it("grants pro to a free session, and every Pro feature with it", () => {
    expect(effectivePlan("free", true)).toBe("pro");
    const body = me("free", true, 7, 10);
    expect(body.plan).toBe("pro");
    expect(body.stored_plan).toBe("free");
    expect(body.hackathon).toBe(true);
    for (const f of FEATURES) expect(shows(body, f)).toBe(true);
  });
});

describe("hackathon mode off", () => {
  it("keeps the stored plan", () => {
    expect(effectivePlan("free", false)).toBe("free");
    expect(effectivePlan("pro", false)).toBe("pro");
  });

  it("hides every Pro feature from a free session", () => {
    const body = me("free", false, 10, 10);
    expect(body.plan).toBe("free");
    expect(body.features).toEqual(entitlements("free"));
    for (const f of FEATURES) expect(shows(body, f)).toBe(false);
  });

  it("shows every Pro feature to a pro session", () => {
    for (const f of FEATURES) expect(shows(me("pro", false, null, 10), f)).toBe(true);
  });
});

it("lists the six Pro features", () => {
  expect(FEATURES).toEqual([
    "multi_venue",
    "full_shortlist",
    "briefs",
    "exports",
    "run_history",
    "artist_routing",
  ]);
});

it("shows everything while the plan is unknown, so nothing blocks on the read", () => {
  for (const f of FEATURES) expect(shows(null, f)).toBe(true);
});

it("carries the runs left and the hourly allowance", () => {
  expect(me("free", false, 3, 10)).toMatchObject({ remaining_runs: 3, runs_per_hour: 10 });
  expect(me("free", false, null, 10).remaining_runs).toBeNull();
});
