// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { crossDomain, type SoundcheckData } from "@/src/lib/soundcheck";
import type { CrowdProfile } from "@/types/schemas";
import { fakeApi } from "../../new/test-utils";
import venue from "../fixtures/venue.json";
import fixture from "./fixtures/soundcheck.json";
import { Soundcheck, SoundcheckButton, resetSoundcheckCache } from "./soundcheck";

const data = fixture as SoundcheckData;
const strip = crossDomain(venue.profile as CrowdProfile);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetSoundcheckCache();
});

function setup(d: SoundcheckData = data) {
  render(<Soundcheck data={d} crossDomain={strip} />);
}

const list = (name: string) => within(screen.getByRole("list", { name }));
const items = (name: string) => list(name).getAllByTestId("trail-item");
const sources = (el: HTMLElement) =>
  within(el)
    .getAllByTestId("source")
    .map((s) => s.dataset.source);

describe("Soundcheck panel", () => {
  it("renders every section of the trail from a fixture", () => {
    setup();

    expect(screen.getByRole("heading", { name: "Why this act" })).toBeTruthy();
    expect(screen.getByText(/recommend puts Synthetic Headliner at 92% affinity/)).toBeTruthy();
    for (const branch of ["Inputs", "Qloo calls", "Affinities", "Score breakdown"]) {
      expect(screen.getByRole("heading", { name: branch })).toBeTruthy();
    }
    expect(screen.getByRole("heading", { name: "What streaming data misses" })).toBeTruthy();

    expect(items("Inputs").map((i) => i.textContent?.replace(/(Qloo|Outdraw)$/, ""))).toEqual([
      "Crowd signal Synthetic Act, weight 0.95 (sell-through)",
      "Venue region: New York, United States",
      "Requested size tier: mid",
      "Qloo popularity 0.812 (recommend)",
      "Never booked at this venue (novelty 1.0)",
    ]);

    const calls = items("Qloo calls");
    expect(calls.map((c) => c.querySelector(".font-mono")?.textContent)).toEqual([
      "recommend",
      "compare_audiences",
      "where_popular",
    ]);
    expect(calls.map((c) => within(c).getByTestId("status").textContent)).toEqual([
      "ok",
      "partial",
      "ok",
    ]);
    expect(calls.map((c) => within(c).getByTestId("cache").textContent)).toEqual([
      "live",
      "cached",
      "live",
    ]);

    expect(items("Affinities").map((a) => a.textContent?.replace(/Qloo$/, ""))).toEqual([
      "Crowd affinity 0.92 from recommend",
      "Past fit 0.81 from compare_audiences",
      "Local heat 0.7 from where_popular",
    ]);

    const breakdown = items("Score breakdown");
    expect(breakdown.map((b) => b.dataset.subScore ?? "total")).toEqual([
      "crowd_affinity",
      "past_fit",
      "local_heat",
      "novelty",
      "size_fit",
      "momentum",
      "total",
    ]);
    expect(breakdown[0].textContent).toContain("weight 0.35 → adds 0.322");
    expect(screen.getByTestId("trail-total").textContent).toBe("0.849");

    const contributors = items("Cross-domain contributors").map((c) => c.textContent);
    expect(contributors).toEqual([
      "PlaceSynthetic Bar 0.67Qloo",
      "BrandSynthetic Brand 0.52Qloo",
      "RegionNew York, United StatesOutdraw",
      "Local heat0.70Qloo",
    ]);
  });

  it("prints sub-scores at the trail's precision so each 'adds' line reproduces by hand", () => {
    // 0.9641 × 0.35 = 0.3374: shown as 0.96 the line could not be checked against the trail.
    const trail = {
      ...data.trail,
      sub_scores: { ...data.trail.sub_scores, crowd_affinity: 0.9641 },
      total: 0.8644,
    };
    setup({ ...data, trail });

    const breakdown = items("Score breakdown");
    expect(breakdown[0].textContent).toContain("Crowd affinity 0.9641");
    expect(breakdown[0].textContent).toContain("weight 0.35 → adds 0.3374");
    expect(screen.getByTestId("trail-total").textContent).toBe("0.8644");
    expect(screen.queryByTestId("rounding-note")).toBeNull();
  });

  it("says so when the rounded lines miss the total by a unit in the last place", () => {
    const trail = {
      ...data.trail,
      sub_scores: { ...data.trail.sub_scores, crowd_affinity: 0.9641 },
      total: 0.8645,
    };
    setup({ ...data, trail });

    expect(screen.getByTestId("trail-total").textContent).toBe("0.8645");
    expect(screen.getByTestId("rounding-note").textContent).toContain("add up to 0.8644");
  });

  it("puts a Qloo or Outdraw label from source_labels on every item", () => {
    setup();

    const all = screen.getAllByTestId("trail-item");
    expect(all.length).toBeGreaterThan(15);
    for (const item of all) {
      const labels = sources(item);
      expect(labels.length).toBeGreaterThan(0);
      for (const label of labels) expect(["qloo", "draw"]).toContain(label);
    }
    // Labels follow the trail, not the item kind.
    expect(items("Inputs").map((i) => sources(i)[0])).toEqual([
      "draw",
      "draw",
      "draw",
      "qloo",
      "draw",
    ]);
    expect(items("Qloo calls").flatMap(sources)).toEqual(["qloo", "qloo", "qloo"]);
    const breakdown = items("Score breakdown");
    // [sub-score, weight] per row; the total row has one.
    expect(breakdown.map(sources)).toEqual([
      ["qloo", "draw"],
      ["qloo", "draw"],
      ["qloo", "draw"],
      ["draw", "draw"],
      ["draw", "draw"],
      ["draw", "draw"],
      ["draw"],
    ]);
    expect(screen.getAllByText("Qloo").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Outdraw").length).toBeGreaterThan(0);
  });

  it("takes the top reason's and the profile lists' labels from the trail, not the UI", () => {
    const why = () => sources(screen.getByRole("heading", { name: "Why this act" }).parentElement!);
    const place = () =>
      sources(items("Cross-domain contributors").find((c) => c.textContent?.startsWith("Place"))!);
    setup();
    expect(why()).toEqual(["draw"]);
    expect(place()).toEqual(["qloo"]);
    cleanup();

    // The agent decides: flip its labels and the badges follow.
    setup({
      ...data,
      trail: {
        ...data.trail,
        source_labels: { ...data.trail.source_labels, top_reason: "qloo", "profile:places": "draw" },
      },
    });
    expect(why()).toEqual(["qloo"]);
    expect(place()).toEqual(["draw"]);
    cleanup();

    // A trail from before these keys existed says so rather than guessing.
    const older = Object.fromEntries(
      Object.entries(data.trail.source_labels).filter(
        ([k]) => k !== "top_reason" && k !== "profile:places",
      ),
    );
    setup({ ...data, trail: { ...data.trail, source_labels: older } });
    expect(why()).toEqual(["none"]);
    expect(place()).toEqual(["none"]);
  });

  it("marks a sub-score with no Qloo signal as the neutral fallback", () => {
    setup();

    const momentum = items("Score breakdown").find((b) => b.dataset.subScore === "momentum")!;
    expect(within(momentum).getByTestId("neutral").textContent).toBe(
      "no Qloo signal, neutral 0.5",
    );
    expect(sources(momentum)[0]).toBe("draw");
    expect(screen.getAllByTestId("neutral")).toHaveLength(1);
  });

  it("shows the envelope summary of partial and degraded calls only", () => {
    const degraded: SoundcheckData = {
      ...data,
      trail: {
        ...data.trail,
        qloo_calls: data.trail.qloo_calls.map((c) =>
          c.workflow === "where_popular" ? { ...c, status: "degraded" } : c,
        ),
      },
    };
    setup(degraded);

    const [recommend, compare, where] = items("Qloo calls");
    expect(within(recommend).queryByTestId("envelope-summary")).toBeNull();
    expect(within(compare).getByTestId("envelope-summary").textContent).toBe(
      "Synthetic summary: 2 of 3 audiences compared; overlap uses the rest.",
    );
    // A degraded call whose envelope carried no summary still says so.
    expect(within(where).getByTestId("envelope-summary").textContent).toBe(
      "Qloo marked this call degraded without a summary.",
    );
  });

  it("renders a trail with no calls, no affinities and no cross-domain data", () => {
    const bare: SoundcheckData = {
      trail: {
        ...data.trail,
        qloo_calls: [],
        affinities: [],
        inputs: data.trail.inputs.filter((i) => i.ref !== "region"),
      },
      summaries: {},
      pencilled: false,
    };
    render(<Soundcheck data={bare} />);

    expect(screen.getByText("No Qloo calls behind this score.")).toBeTruthy();
    expect(screen.getByText("No Qloo affinity behind this score.")).toBeTruthy();
    expect(screen.getByText("Qloo returned no places, films or brands for this crowd.")).toBeTruthy();
  });
});

describe("Soundcheck slide-over", () => {
  const entityId = "urn:entity:synthetic:artist:11";

  function open() {
    render(
      <SoundcheckButton
        runId="run_fixture"
        entityId={entityId}
        name="Synthetic Headliner"
        crossDomain={strip}
      />,
    );
    return userEvent.setup();
  }

  it("loads the trail on open, closes on Escape and hands focus back", async () => {
    let answer!: (r: Response) => void;
    const calls = fakeApi([[/\/soundcheck$/, () => new Promise<Response>((r) => (answer = r))]]);
    const user = open();

    const trigger = screen.getByRole("button", { name: "Soundcheck for Synthetic Headliner" });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Synthetic Headliner" });
    expect(within(dialog).getByRole("status").textContent).toContain("Loading the evidence trail");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close" }));
    answer(Response.json(fixture));
    expect(await within(dialog).findByRole("heading", { name: "Why this act" })).toBeTruthy();
    expect(calls).toEqual([
      {
        method: "GET",
        url: `/api/runs/run_fixture/candidates/${encodeURIComponent(entityId)}/soundcheck`,
        body: undefined,
      },
    ]);
    // A slide-over from the right at sm and up, a bottom sheet below 640px.
    const panel = screen.getByTestId("soundcheck-panel");
    expect(panel.className).toMatch(/\binset-x-0 bottom-0\b/);
    expect(panel.className).toMatch(/\bsm:right-0\b/);
    expect(panel.className).toMatch(/\bsm:w-\[28rem\]/);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);

    // Reopening reads the loaded trail, not the API.
    await user.click(trigger);
    expect(screen.getByRole("heading", { name: "Why this act" })).toBeTruthy();
    expect(calls).toHaveLength(1);
  });

  it("shows the reason and retries when the trail fails to load", async () => {
    fakeApi([
      [/\/soundcheck$/, Response.json({ reason: "unavailable" }, { status: 503 })],
      [/\/soundcheck$/, Response.json(fixture)],
    ]);
    const user = open();

    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach Outdraw");

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Why this act" })).toBeTruthy();
  });

  it("keeps Tab inside the panel while it is open", async () => {
    fakeApi([[/\/soundcheck$/, Response.json(fixture)]]);
    const user = open();
    // Something focusable behind the panel, which Tab must never reach.
    const behind = document.createElement("button");
    behind.textContent = "Behind";
    document.body.append(behind);

    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("heading", { name: "Why this act" });
    const closeButton = within(dialog).getByRole("button", { name: "Close" });
    expect(document.activeElement).toBe(closeButton);

    // Shift+Tab from the first control wraps to the last; Tab from the last wraps back.
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(closeButton);
    await user.tab();
    expect(document.activeElement).toBe(closeButton);

    // Tabbing all the way through never lands outside the panel.
    for (let i = 0; i < 12; i++) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    expect(document.activeElement).not.toBe(behind);
    behind.remove();
  });

  it("closes from the backdrop", async () => {
    fakeApi([[/\/soundcheck$/, Response.json(fixture)]]);
    const user = open();

    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    await user.click(screen.getByTestId("backdrop"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
