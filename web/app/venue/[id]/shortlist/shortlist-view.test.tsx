// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { defaultConstraints, parseRun, type ShortlistResult } from "@/src/lib/run-result";
import type { RunOut } from "@/types/agent";
import fixture from "./fixtures/run.json";
import { RunOutcome } from "./run-outcome";
import { ShortlistSkeleton, ShortlistView } from "./shortlist-view";

const run = fixture as RunOut;

afterEach(cleanup);

function shortlist(): ShortlistResult {
  const parsed = parseRun(run);
  if (parsed.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  return parsed;
}

function setup(result: ShortlistResult = shortlist()) {
  render(
    <ShortlistView
      venueId="ven_fixture"
      venueName="Synthetic Room"
      runId="run_fixture"
      result={result}
    />,
  );
}

const acts = () => within(screen.getByRole("list", { name: "Ranked acts" })).getAllByRole("heading");
const chips = (name: string) =>
  within(screen.getByRole("list", { name: `Sub-scores for ${name}` })).getAllByTestId("chip");

describe("shortlist view", () => {
  it("lists the acts in the run's order with their totals as bars", () => {
    setup();

    expect(screen.getByRole("heading", { level: 1, name: "Synthetic Room" })).toBeTruthy();
    expect(acts().map((h) => h.textContent)).toEqual([
      "Synthetic Headliner",
      "Fixture Collective",
      "Test Pattern Trio",
    ]);
    expect(screen.getAllByTestId("total").map((t) => t.textContent)).toEqual([
      "Score 0.81",
      "Score 0.65",
      "Score 0.41",
    ]);
    const [bar] = screen.getAllByTestId("total-bar");
    expect(bar.getAttribute("aria-hidden")).toBe("true");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("81%");
  });

  it("shows three decimals where two neighbours round to the same score", () => {
    const result = shortlist();
    const [first, second, third] = result.shortlist;
    // 0.863 and 0.858 both read 0.86 at two decimals; the third act is clear of them.
    setup({
      ...result,
      shortlist: [
        { ...first, total: 0.863 },
        { ...second, total: 0.858 },
        { ...third, total: 0.41 },
      ],
    });

    expect(screen.getAllByTestId("total").map((t) => t.textContent)).toEqual([
      "Score 0.863",
      "Score 0.858",
      "Score 0.41",
    ]);
    expect(screen.getAllByTestId("total")[0].getAttribute("title")).toMatch(/same score/);
    expect(screen.getAllByTestId("total")[2].getAttribute("title")).toBeNull();
  });

  it("shows each act's top reason", () => {
    setup();

    expect(screen.getByText(/recommend puts Synthetic Headliner at 92% affinity/)).toBeTruthy();
    expect(screen.getByText(/where_popular heatmap gives Fixture Collective/)).toBeTruthy();
  });

  it("labels every sub-score chip by source, with neutral fallbacks marked", () => {
    setup();

    const head = chips("Synthetic Headliner");
    expect(head.map((c) => c.textContent)).toEqual([
      "Crowd affinity 0.92 (Qloo data)",
      "Past fit 0.81 (Qloo data)",
      "Local heat 0.70 (Qloo data)",
      "Novelty 1.00 (Outdraw)",
      "Size fit 1.00 (Outdraw)",
      "Momentum 0.50 (no Qloo signal, run median)",
    ]);
    expect(head.map((c) => c.dataset.source)).toEqual([
      "qloo",
      "qloo",
      "qloo",
      "outdraw",
      "outdraw",
      "neutral",
    ]);
    expect(chips("Fixture Collective")[1].dataset.source).toBe("neutral");
  });

  it("renders an act without sub-scores or a reason without inventing them", () => {
    setup();

    const row = screen.getByRole("heading", { name: "Test Pattern Trio" }).closest("li")!;
    expect(within(row).queryAllByTestId("chip")).toEqual([]);
    expect(row.querySelector("p")).toBeNull();
  });

  it("labels partial data as Outdraw's reading and lists the notes", () => {
    setup();

    const banner = within(screen.getByRole("complementary", { name: "Data quality" }));
    expect(banner.getByText("Some Qloo signals behind this shortlist are partial.")).toBeTruthy();
    expect(banner.getByText("Outdraw")).toBeTruthy();
    expect(banner.getByText(/compare_audiences call came back empty/)).toBeTruthy();
  });

  it("says so when no act made the list", () => {
    setup({ ...shortlist(), shortlist: [], data_quality: "ok", notes: [] });

    expect(screen.getByRole("alert").textContent).toContain("No acts made the shortlist");
    expect(screen.queryByRole("complementary", { name: "Data quality" })).toBeNull();
  });

  it("links back to the crowd profile", () => {
    setup();

    const link = screen.getByRole("link", { name: "Back to crowd profile" });
    expect(link.getAttribute("href")).toBe("/venue/ven_fixture");
  });

  it("renders a skeleton with the list's footprint", () => {
    render(<ShortlistSkeleton />);

    expect(screen.getByRole("status", { name: "Loading shortlist" })).toBeTruthy();
    expect(screen.getAllByTestId("skeleton-act")).toHaveLength(5);
  });
});

describe("run outcome", () => {
  const outcome = (r: RunOut) =>
    render(<RunOutcome venueId="ven_fixture" venueName="Synthetic Room" run={parseRun(r)} />);

  it("shows the agent's question for an ask run", () => {
    outcome({
      ...run,
      status: "ask",
      result: {
        ask: {
          question: "Which Ambiguous Band did you mean?",
          candidates: [{ name: "Ambiguous Band" }, { name: "Ambiguous Band (UK)" }],
        },
        data_quality: "partial",
      },
    });

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Which Ambiguous Band did you mean?");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows a generic failure for an error run or a result with nothing to show", () => {
    outcome({
      ...run,
      status: "error",
      result: { error: "Traceback (most recent call last)", data_quality: "degraded" },
    });

    expect(screen.getByRole("alert").textContent).toContain("Something went wrong");
    expect(screen.getByRole("alert").textContent).not.toContain("Traceback");
    cleanup();

    outcome({ ...run, result: null });
    expect(screen.getByRole("alert").textContent).toContain("Couldn't build the shortlist");
  });

  it("marks chips by the agent's source labels, not by sub-score name", () => {
    const base = shortlist();
    const [head] = base.shortlist;
    // The agent, not the UI, decides what counts as Qloo data: flip its labels and the
    // chips follow.
    const flipped = {
      ...head,
      neutral: [],
      source_labels: { ...head.source_labels, crowd_affinity: "draw", novelty: "qloo" },
    } satisfies typeof head;
    render(
      <ShortlistView
        venueId="ven_fixture"
        venueName="Synthetic Room"
        runId="run_fixture"
        result={{ ...base, shortlist: [flipped] }}
      />,
    );

    const sources = Object.fromEntries(
      chips("Synthetic Headliner").map((c) => [
        c.textContent?.replace(/ \d.*$/, ""),
        c.dataset.source,
      ]),
    );
    expect(sources["Crowd affinity"]).toBe("outdraw");
    expect(sources["Novelty"]).toBe("qloo");
    expect(sources["Momentum"]).toBe("outdraw");
  });
});

describe("default constraints", () => {
  it("picks the size tier from capacity and filters nothing else", () => {
    expect(defaultConstraints(150)).toEqual({
      include_tags: [],
      exclude_tags: [],
      size_tier: "emerging",
      exclude_recent_months: 6,
      max_candidates: 40,
    });
    expect(defaultConstraints(400).size_tier).toBe("mid");
    expect(defaultConstraints(1_500).size_tier).toBe("established");
  });
});
