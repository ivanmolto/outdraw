// @vitest-environment jsdom
// A seed run's panels read the page's precomputed data (src/lib/precomputed.tsx): the
// Soundcheck trail, the briefs precompute drafted and the LLM-only list, with no fetch at all.
// Any other run on the page still fetches, as a live run does.
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PrecomputedProvider, type Precomputed } from "@/src/lib/precomputed";
import { parseRun } from "@/src/lib/run-result";
import type { SoundcheckData } from "@/src/lib/soundcheck";
import type { BriefOut, CompareOut, RunOut } from "@/types/agent";
import { fakeApi } from "../../new/test-utils";
import brief from "./fixtures/brief.json";
import compare from "./fixtures/compare.json";
import fixture from "./fixtures/run.json";
import soundcheck from "./fixtures/soundcheck.json";
import { resetBriefCache } from "./brief";
import { resetCompareCache } from "./compare-toggle";
import { resetSoundcheckCache } from "./soundcheck";
import { ShortlistView } from "./shortlist-view";

const HEADLINER = "urn:entity:synthetic:artist:11";
const SECOND = "urn:entity:synthetic:artist:12";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetSoundcheckCache();
  resetBriefCache();
  resetCompareCache();
});

const trail = (entityId: string, name: string): SoundcheckData => {
  const data = soundcheck as SoundcheckData;
  return { ...data, trail: { ...data.trail, entity_id: entityId, name } };
};

function precomputed(over: Partial<Precomputed> = {}): Precomputed {
  return {
    runId: "run_fixture",
    soundcheck: {
      [HEADLINER]: trail(HEADLINER, "Synthetic Headliner"),
      [SECOND]: trail(SECOND, "Fixture Collective"),
    },
    briefs: { [HEADLINER]: brief as BriefOut },
    compare: compare as CompareOut,
    ...over,
  };
}

function setup(value: Precomputed, runId = "run_fixture") {
  const run = parseRun(fixture as RunOut);
  if (run.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  render(
    <PrecomputedProvider value={value}>
      <ShortlistView venueId="ven_fixture" venueName="Synthetic Room" runId={runId} result={run} />
    </PrecomputedProvider>,
  );
  return userEvent.setup();
}

describe("precomputed seed run", () => {
  it("opens Soundcheck and its brief from the page's data, with no fetch", async () => {
    const calls = fakeApi([]);
    const user = setup(precomputed());

    expect(screen.getByTestId("precomputed-label").textContent).toBe("Precomputed demo");
    await user.click(screen.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }));
    expect(screen.getByRole("heading", { name: "Why this act" })).toBeTruthy();
    // Seed runs are read-only: no pencil hold.
    expect(screen.queryByRole("switch", { name: "Pencil hold" })).toBeNull();
    expect(screen.getByText(/Pencil holds are off on the demo/)).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "Brief" }));
    expect(screen.getByRole("heading", { name: "Outreach email" })).toBeTruthy();
    expect(calls).toEqual([]);
  });

  it("says a brief was not precomputed instead of asking the agent for one", async () => {
    const calls = fakeApi([]);
    const user = setup(precomputed());

    await user.click(screen.getByRole("button", { name: "Soundcheck for Fixture Collective" }));
    await user.click(screen.getByRole("tab", { name: "Brief" }));
    expect(screen.getByRole("status").textContent).toContain(
      "The demo has briefs for its top acts only.",
    );
    expect(calls).toEqual([]);
  });

  it("shows the precomputed LLM-only list, or says there is none", async () => {
    const calls = fakeApi([]);
    let user = setup(precomputed());
    await user.click(screen.getByRole("switch", { name: "With Qloo / LLM only" }));
    expect(screen.getByRole("list", { name: "LLM-only acts" })).toBeTruthy();
    cleanup();

    user = setup(precomputed({ compare: null }));
    await user.click(screen.getByRole("switch", { name: "With Qloo / LLM only" }));
    expect(screen.getByText(/This demo has no LLM-only list/)).toBeTruthy();
    expect(calls).toEqual([]);
  });

  it("leaves any other run live: no label, and its panels fetch", async () => {
    const calls = fakeApi([
      [/\/api\/runs\/run_live\/candidates\/.+\/soundcheck$/, Response.json(soundcheck)],
    ]);
    const user = setup(precomputed(), "run_live");

    expect(screen.queryByTestId("precomputed-label")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Soundcheck for Synthetic Headliner" }));
    expect(await screen.findByRole("switch", { name: "Pencil hold" })).toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual([
      `/api/runs/run_live/candidates/${encodeURIComponent(HEADLINER)}/soundcheck`,
    ]);
  });
});
