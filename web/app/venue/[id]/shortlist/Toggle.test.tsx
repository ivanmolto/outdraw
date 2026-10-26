// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseRun } from "@/src/lib/run-result";
import type { RunOut } from "@/types/agent";
import { fakeApi } from "../../new/test-utils";
import compare from "./fixtures/compare.json";
import fixture from "./fixtures/run.json";
import { resetCompareCache } from "./compare-toggle";
import { ShortlistView } from "./shortlist-view";

const COMPARE = /\/api\/runs\/run_fixture\/compare$/;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetCompareCache();
});

/** A viewport `width` px wide, as `matchMedia` reports it (jsdom has no layout). */
function viewport(width: number) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal(
    "matchMedia",
    vi.fn((media: string) => {
      const max = /max-width:\s*(\d+)px/.exec(media);
      return {
        media,
        matches: max ? width <= Number(max[1]) : false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      };
    }),
  );
}

function setup() {
  const run = parseRun(fixture as RunOut);
  if (run.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  render(
    <ShortlistView venueId="ven_fixture" venueName="Synthetic Room" runId="run_fixture" result={run} />,
  );
  return userEvent.setup();
}

const toggle = () => screen.getByRole("switch", { name: "With Qloo / LLM only" });

/** The mobile layout as structure: the tabs, which is selected, and what each panel holds
 * (its list's name and length, or that it is hidden). Not the full DOM: React's `useId`
 * values and Tailwind classes would make the snapshot churn on every unrelated change. */
function mobileLayout(root: HTMLElement) {
  const tabs = within(root)
    .getAllByRole("tab")
    .map((tab) => ({
      label: tab.textContent,
      selected: tab.getAttribute("aria-selected") === "true",
      panel: panelOf(tab),
    }));
  return { columns: root.querySelector("[data-testid=compare-columns]") !== null, tabs };
}

function panelOf(tab: HTMLElement) {
  const panel = document.getElementById(tab.getAttribute("aria-controls")!)!;
  const list = panel.querySelector("ol");
  return {
    hidden: panel.hidden,
    list: list?.getAttribute("aria-label") ?? null,
    // Plain DOM, not roles: a hidden panel's headings are not in the accessibility tree.
    acts: list ? Array.from(list.querySelectorAll("h2, h3"), (h) => h.textContent) : [],
  };
}
const names = (list: HTMLElement) =>
  within(list)
    .getAllByRole("heading")
    .map((h) => h.textContent);

describe("With Qloo / LLM only toggle", () => {
  it("starts off with Outdraw's list alone and asks for nothing", () => {
    viewport(1280);
    const calls = fakeApi([]);
    setup();

    expect(toggle().getAttribute("aria-checked")).toBe("false");
    expect(screen.getByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(screen.queryByRole("list", { name: "LLM-only acts" })).toBeNull();
    expect(calls).toEqual([]);
  });

  it("puts the two lists side by side on desktop, with the overlap marked", async () => {
    viewport(1280);
    const calls = fakeApi([[COMPARE, Response.json(compare)]]);
    const user = setup();

    await user.click(toggle());

    expect(toggle().getAttribute("aria-checked")).toBe("true");
    const columns = screen.getByTestId("compare-columns");
    expect(within(columns).getByRole("heading", { name: "With Qloo" })).toBeTruthy();
    expect(within(columns).getByRole("heading", { name: "LLM only" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();

    const llm = await screen.findByRole("list", { name: "LLM-only acts" });
    const qloo = screen.getByRole("list", { name: "Ranked acts" });
    expect(names(qloo)).toEqual(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);
    expect(names(llm)).toEqual(["Synthetic Headliner", "Imaginary Arena Band", "Placeholder Duo"]);
    expect(screen.getByTestId("disclaimer").textContent).toContain("no Qloo data");
    expect(screen.getByTestId("overlap").textContent).toBe("1 of 3 also on Outdraw's shortlist.");
    const rows = within(llm).getAllByRole("listitem");
    expect(rows.map((r) => r.dataset.overlap)).toEqual(["true", "false", "false"]);
    expect(rows[1].textContent).toContain("Not on Outdraw's shortlist · no Qloo evidence · not scored");
    expect(calls).toEqual([{ method: "GET", url: "/api/runs/run_fixture/compare", body: undefined }]);

    // Off and on again reads the list already fetched.
    await user.click(toggle());
    expect(screen.queryByTestId("compare-columns")).toBeNull();
    await user.click(toggle());
    expect(screen.getByRole("list", { name: "LLM-only acts" })).toBeTruthy();
    expect(calls).toHaveLength(1);
  });

  it("swaps the lists with tabs on mobile", async () => {
    viewport(375);
    fakeApi([[COMPARE, Response.json(compare)]]);
    const user = setup();

    await user.click(toggle());

    expect(screen.queryByTestId("compare-columns")).toBeNull();
    const tabs = within(screen.getByRole("tablist", { name: "With Qloo / LLM only" }));
    const [withQloo, llmOnly] = tabs.getAllByRole("tab");
    const panel = (tab: HTMLElement) => document.getElementById(tab.getAttribute("aria-controls")!)!;

    expect(withQloo.getAttribute("aria-selected")).toBe("true");
    expect(panel(withQloo).hidden).toBe(false);
    expect(panel(llmOnly).hidden).toBe(true);
    expect(within(panel(withQloo)).getByRole("list", { name: "Ranked acts" })).toBeTruthy();

    await user.click(llmOnly);

    expect(llmOnly.getAttribute("aria-selected")).toBe("true");
    expect(panel(withQloo).hidden).toBe(true);
    expect(panel(llmOnly).hidden).toBe(false);
    expect(await within(panel(llmOnly)).findByRole("list", { name: "LLM-only acts" })).toBeTruthy();

    await user.click(withQloo);
    expect(panel(withQloo).hidden).toBe(false);
    expect(panel(llmOnly).hidden).toBe(true);
  });

  it("matches the mobile layout snapshot at 375px", async () => {
    viewport(375);
    fakeApi([[COMPARE, Response.json(compare)]]);
    const user = setup();

    await user.click(toggle());
    await user.click(screen.getByRole("tab", { name: "LLM only" }));
    await screen.findByRole("list", { name: "LLM-only acts" });

    expect(window.innerWidth).toBe(375);
    expect(mobileLayout(screen.getByTestId("compare-tabs"))).toMatchSnapshot();
  });

  it("says why when the LLM-only list can't be had", async () => {
    viewport(1280);
    fakeApi([[COMPARE, Response.json({ reason: "unavailable" }, { status: 503 })]]);
    const user = setup();

    await user.click(toggle());

    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach Outdraw");
  });

  it.each([
    [429, "budget"],
    [503, "live_mode_off"],
  ])("says the list waits for live analysis on %i %s, with no error", async (status, reason) => {
    viewport(1280);
    fakeApi([[COMPARE, Response.json({ reason, fallback_seed: "nola-jazz" }, { status })]]);
    const user = setup();

    await user.click(toggle());

    const paused = await screen.findByRole("status", { name: "Live analysis paused" });
    expect(paused.textContent).toContain("LLM-only list can't be fetched right now");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("never shows the agent's error text for a list the model botched", async () => {
    viewport(1280);
    fakeApi([
      [COMPARE, Response.json({ ...compare, acts: [], error: "The model hit max_tokens." })],
    ]);
    const user = setup();

    await user.click(toggle());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("The model gave no usable list for this run.");
  });
});
