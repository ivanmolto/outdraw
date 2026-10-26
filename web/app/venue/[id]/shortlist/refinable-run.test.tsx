// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseRun } from "@/src/lib/run-result";
import type { RunOut } from "@/types/agent";
import { fakeApi } from "../../new/test-utils";
import parentFixture from "./fixtures/run.json";
import refinedFixture from "./fixtures/refined-run.json";
import { seedSample } from "./fixtures/sample";
import { RefinableRun } from "./refinable-run";

const REFINE = (runId: string) => new RegExp(`/api/runs/${runId}/refine$`);
const parent = parentFixture as RunOut;
const refined = refinedFixture as RunOut;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup() {
  const replace = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
  render(<RefinableRun venueId="ven_fixture" venueName="Synthetic Room" chain={[parseRun(parent)]} />);
  return { user: userEvent.setup(), replace };
}

const acts = () =>
  within(screen.getByRole("list", { name: "Ranked acts" }))
    .getAllByRole("heading")
    .map((h) => h.textContent);
const history = () => screen.getByRole("complementary", { name: "Run history" });

/** A child of `run` named `runId`: the same list reversed. */
function child(run: RunOut, runId: string, message: string): RunOut {
  return {
    ...run,
    run_id: runId,
    parent_run_id: run.run_id,
    result: {
      ...run.result!,
      shortlist: [...(run.result!.shortlist ?? [])].reverse(),
      refinement: { message, delta: {}, explanation: `Reversed for “${message}”.` },
    },
  };
}

describe("refinable run", () => {
  it("shows the chips and no history for a first run", () => {
    setup();

    const chips = screen.getByRole("list", { name: "Suggested refinements" });
    expect(within(chips).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "More emerging",
      "Younger crowd",
      "Exclude tributes",
      "Bigger acts",
    ]);
    expect(screen.queryByRole("complementary", { name: "Run history" })).toBeNull();
  });

  it("refines on a chip click and renders the new list with its diff", async () => {
    const calls = fakeApi([[REFINE("run_fixture"), Response.json(refined)]]);
    const { user, replace } = setup();

    await user.click(screen.getByRole("button", { name: "More emerging" }));

    expect(await screen.findByText("Emerging Fixture Quartet")).toBeTruthy();
    expect(calls).toEqual([
      {
        method: "POST",
        url: "/api/runs/run_fixture/refine",
        body: { message: "More emerging acts" },
      },
    ]);
    expect(acts()).toEqual(["Fixture Collective", "Emerging Fixture Quartet", "Synthetic Headliner"]);
    expect(screen.getAllByTestId("change").map((b) => b.dataset.change)).toEqual([
      "moved",
      "entered",
      "moved",
    ]);
    expect(
      within(screen.getByRole("region", { name: "Left the list" })).getByText("Test Pattern Trio"),
    ).toBeTruthy();
    expect(replace).toHaveBeenLastCalledWith(null, "", "?run=run_fixture_refined");

    const runs = within(history()).getAllByRole("button");
    expect(runs).toHaveLength(2);
    expect(runs[1].getAttribute("aria-current")).toBe("true");
    expect(runs[1].textContent).toContain("“More emerging acts”");
    expect(runs[1].textContent).toContain("Moved the size tier from mid to emerging");
  });

  it("chains three successive refinements, each to the run before it", async () => {
    const calls = fakeApi([
      [REFINE("run_fixture"), Response.json(refined)],
      [REFINE("run_fixture_refined"), (c) =>
        Response.json(child(refined, "run_2", (c.body as { message: string }).message))],
      [REFINE("run_2"), (c) =>
        Response.json(child(child(refined, "run_2", "x"), "run_3", (c.body as { message: string }).message))],
    ]);
    const { user } = setup();

    await user.click(screen.getByRole("button", { name: "More emerging" }));
    await screen.findByText("Emerging Fixture Quartet");
    await user.click(screen.getByRole("button", { name: "Exclude tributes" }));
    await within(history()).findByText("“No tribute acts”");
    await user.type(screen.getByLabelText("Or say what to change"), "Cap it at 300{Enter}");
    await within(history()).findByText("“Cap it at 300”");

    expect(calls.map((c) => [c.url, c.body])).toEqual([
      ["/api/runs/run_fixture/refine", { message: "More emerging acts" }],
      ["/api/runs/run_fixture_refined/refine", { message: "No tribute acts" }],
      ["/api/runs/run_2/refine", { message: "Cap it at 300" }],
    ]);
    expect(within(history()).getAllByRole("button")).toHaveLength(4);
    // The last two refinements each reverse their parent's list: the ends swap places.
    expect(acts()).toEqual(["Fixture Collective", "Emerging Fixture Quartet", "Synthetic Headliner"]);
    expect(screen.getAllByTestId("change").map((b) => b.textContent)).toEqual([
      expect.stringContaining("↑2"),
      expect.stringContaining("↓2"),
    ]);
  });

  it("shows an earlier run from the history with its own diff, and branches from it", async () => {
    fakeApi([
      [REFINE("run_fixture"), Response.json(refined)],
      [REFINE("run_fixture"), Response.json(child(parent, "run_b", "Bigger acts"))],
    ]);
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "More emerging" }));
    await screen.findByText("Emerging Fixture Quartet");

    await user.click(within(history()).getByRole("button", { name: /First run/ }));
    expect(acts()).toEqual(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);
    expect(screen.queryAllByTestId("change")).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Bigger acts" }));
    await within(history()).findByText("“Bigger acts”");
    const runs = within(history()).getAllByRole("button");
    expect(runs.map((r) => r.textContent?.includes("More emerging"))).toEqual([false, false]);
  });

  it("explains a message the agent could read no change from", async () => {
    fakeApi([[REFINE("run_fixture"), Response.json({ reason: "invalid_input" }, { status: 400 })]]);
    const { user } = setup();

    await user.type(screen.getByLabelText("Or say what to change"), "hello");
    await user.click(screen.getByRole("button", { name: "Refine" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/couldn't read a change/);
    expect(acts()).toEqual(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);
  });

  it.each([
    [429, "budget"],
    [503, "live_mode_off"],
  ])("swaps in the named seed's sample on a %i %s refinement, and goes back", async (status, reason) => {
    const { user, replace } = setup();
    fakeApi([
      [REFINE(parent.run_id), Response.json({ reason, fallback_seed: "london-comedy" }, { status })],
      [/\/api\/seeds\/london-comedy$/, Response.json(seedSample("london-comedy"))],
    ]);

    await user.click(screen.getByRole("button", { name: "Bigger acts" }));

    const banner = await screen.findByRole("status", { name: "Sample shortlist" });
    expect(banner.textContent).toContain("Live analysis is busy, here's a sample");
    expect(screen.getByRole("heading", { name: "Synthetic Seed london-comedy" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("list", { name: "Suggested refinements" })).toBeNull();
    expect(replace).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Back to your shortlist" }));
    expect(screen.getByRole("heading", { name: "Synthetic Room" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "Suggested refinements" })).toBeTruthy();
  });
});
