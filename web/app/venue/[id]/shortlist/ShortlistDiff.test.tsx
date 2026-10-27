// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { parseRun, type ShortlistResult } from "@/src/lib/run-result";
import { diffShortlists } from "@/src/lib/shortlist-diff";
import type { RunOut } from "@/types/agent";
import parentFixture from "./fixtures/run.json";
import refinedFixture from "./fixtures/refined-run.json";
import { ShortlistView } from "./shortlist-view";

afterEach(cleanup);

function shortlist(run: unknown): ShortlistResult {
  const parsed = parseRun(run as RunOut);
  if (parsed.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  return parsed;
}

// Parent: Synthetic Headliner, Fixture Collective, Test Pattern Trio.
// Refined: Fixture Collective, Emerging Fixture Quartet, Synthetic Headliner.
const before = shortlist(parentFixture);
const after = shortlist(refinedFixture);
const id = (n: number) => `urn:entity:synthetic:artist:${n}`;

describe("diffShortlists", () => {
  it("classifies entered, moved and left acts from two fixture lists", () => {
    const diff = diffShortlists(before.shortlist, after.shortlist);

    expect(Object.fromEntries(diff.changes)).toEqual({
      [id(12)]: { kind: "moved", from: 2, by: 1 },
      [id(14)]: { kind: "entered" },
      [id(11)]: { kind: "moved", from: 1, by: -2 },
    });
    expect(diff.left.map(({ entry, rank }) => [entry.name, rank])).toEqual([
      ["Test Pattern Trio", 3],
    ]);
  });

  it("marks an unchanged list as all the same, with nothing left", () => {
    const diff = diffShortlists(before.shortlist, before.shortlist);

    expect([...diff.changes.values()].every((c) => c.kind === "same")).toBe(true);
    expect(diff.left).toEqual([]);
  });

  it("marks every act entered and every old act left when nothing overlaps", () => {
    const diff = diffShortlists(before.shortlist.slice(0, 1), before.shortlist.slice(1));

    expect([...diff.changes.values()]).toEqual([{ kind: "entered" }, { kind: "entered" }]);
    expect(diff.left.map((l) => l.entry.name)).toEqual(["Synthetic Headliner"]);
  });
});

describe("shortlist view with a diff", () => {
  function setup() {
    render(
      <ShortlistView
        venueId="ven_fixture"
        venueName="Synthetic Room"
        runId="run_fixture_refined"
        result={after}
        diff={diffShortlists(before.shortlist, after.shortlist)}
      />,
    );
  }

  const row = (name: string) =>
    screen.getByRole("heading", { name }).closest("li") as HTMLElement;

  it("badges entered acts green and moved acts with an arrow", () => {
    setup();

    const entered = row("Emerging Fixture Quartet");
    expect(entered.dataset.change).toBe("entered");
    expect(within(entered).getByTestId("change").textContent).toBe("New");
    expect(within(entered).getByTestId("change").className).toMatch(/\bgood\b/);

    const up = within(row("Fixture Collective")).getByTestId("change");
    expect(up.dataset.change).toBe("moved");
    expect(up.textContent).toContain("↑1");
    expect(within(up).getByText("Moved up 1 place from #2")).toBeTruthy();

    const down = within(row("Synthetic Headliner")).getByTestId("change");
    expect(down.textContent).toContain("↓2");
    expect(within(down).getByText("Moved down 2 places from #1")).toBeTruthy();
  });

  it("lists the acts that left, greyed out with their old rank", () => {
    setup();

    const left = screen.getByRole("region", { name: "Left the list" });
    const items = within(left).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["3Test Pattern Trio0.41"]);
    expect(items[0].dataset.change).toBe("left");
    expect(items[0].className).toMatch(/text-ink\/45/);
  });

  it("shows no badges or left list without a diff", () => {
    render(
      <ShortlistView
        venueId="ven_fixture"
        venueName="Synthetic Room"
        runId="run_fixture"
        result={before}
      />,
    );

    expect(screen.queryAllByTestId("change")).toEqual([]);
    expect(screen.queryByRole("region", { name: "Left the list" })).toBeNull();
  });
});
