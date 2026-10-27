// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrecomputedProvider } from "@/src/lib/precomputed";
import type { PlayedHereMap } from "@/src/lib/played-here";
import { parseRun, type ShortlistResult } from "@/src/lib/run-result";
import type { RunOut } from "@/types/agent";
import { PencilToggle } from "./brief";
import fixture from "./fixtures/pencilled-run.json";
import plain from "./fixtures/run.json";
import { HoldsCalendar, mergeHolds, resetHoldsCache } from "./holds-calendar";
import { NO_GENRE, filterActs, type BrowsableAct } from "./shortlist-filters";
import { HoldsProvider } from "./holds";
import { ShortlistView } from "./shortlist-view";

const HEADLINER = "urn:entity:synthetic:artist:11";
const COLLECTIVE = "urn:entity:synthetic:artist:12";

function shortlist(run: RunOut = fixture as RunOut): ShortlistResult & { runId: string } {
  const parsed = parseRun(run);
  if (parsed.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  return parsed;
}

/** The venue's holds across runs, as /api/venues/{id}/holds answers; [] by default. */
function holdsApi(holds: unknown[] = []) {
  const fetch = vi.fn(async (url: string) =>
    url.endsWith("/holds")
      ? Response.json({ venue_id: "ven_fixture", holds })
      : Response.json({ reason: "not_found" }, { status: 404 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function setup({
  result = shortlist(),
  played,
  otherHolds = [],
}: {
  result?: ShortlistResult & { runId: string };
  played?: PlayedHereMap;
  otherHolds?: unknown[];
} = {}) {
  const fetch = holdsApi(otherHolds);
  render(
    <ShortlistView
      venueId="ven_fixture"
      venueName="Synthetic Room"
      runId={result.runId}
      result={result}
      played={played}
    />,
  );
  return { user: userEvent.setup(), fetch };
}

const acts = () =>
  within(screen.getByRole("list", { name: "Ranked acts" }))
    .queryAllByRole("heading")
    .map((h) => h.textContent);
const filters = () => within(screen.getByRole("region", { name: "Filters" }));

beforeEach(() => {
  // The calendar opens on the first month from today with a hold: pin today.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-27T12:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetHoldsCache();
});

describe("shortlist filters", () => {
  it("offers the acts' genre tags and capacity bands", () => {
    setup();

    const genres = within(filters().getByRole("group", { name: /Genre/ }));
    // Most common first, then by name.
    expect(genres.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Fixture Rock",
      "Synthetic Wave",
      "Test Folk",
    ]);
    expect(genres.queryByText(/tagged/)).toBeNull();
    const bands = within(filters().getByRole("group", { name: /Capacity band/ }));
    expect(bands.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Any size",
      "Emerging (rooms under 300)",
      "Mid (rooms of 300 to 1,500)",
    ]);
    expect(screen.getByTestId("filter-count").textContent).toBe("All 3 acts.");
  });

  it("narrows the list in the browser, keeping the score's order and ranks", async () => {
    const { user, fetch } = setup();

    await user.click(filters().getByRole("button", { name: "Fixture Rock" }));
    expect(acts()).toEqual(["Synthetic Headliner", "Fixture Collective"]);
    expect(screen.getByTestId("filter-count").textContent).toMatch(/Showing 2 of 3 acts/);

    // Any chosen genre lets an act through.
    await user.click(filters().getByRole("button", { name: "Test Folk" }));
    expect(acts()).toEqual(["Synthetic Headliner", "Fixture Collective", "Test Pattern Trio"]);

    await user.click(filters().getByRole("button", { name: "Mid (rooms of 300 to 1,500)" }));
    expect(acts()).toEqual(["Synthetic Headliner", "Test Pattern Trio"]);
    const ranks = within(screen.getByRole("list", { name: "Ranked acts" }))
      .getAllByRole("listitem")
      .filter((li) => li.parentElement?.getAttribute("aria-label") === "Ranked acts")
      .map((li) => li.querySelector("span")?.textContent);
    expect(ranks).toEqual(["1", "3"]);

    await user.click(filters().getByRole("button", { name: "Clear filters" }));
    expect(acts()).toHaveLength(3);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("says so when no act matches", async () => {
    const { user } = setup();

    await user.click(filters().getByRole("button", { name: "Synthetic Wave" }));
    await user.click(filters().getByRole("button", { name: "Emerging (rooms under 300)" }));

    expect(acts()).toEqual([]);
    expect(screen.getByRole("status").textContent).toContain("No acts match these filters");
  });

  it("keeps acts without genre tags reachable, and says how many there are", async () => {
    const result = shortlist();
    const [first, second, third] = result.shortlist;
    const { user } = setup({
      result: {
        ...result,
        shortlist: [first, { ...second, genres: [] }, { ...third, genres: [] }],
      },
    });

    const genres = within(filters().getByRole("group", { name: /Genre/ }));
    expect(genres.getByText("Qloo tagged 1 of 3 acts", { exact: false })).toBeTruthy();
    await user.click(genres.getByRole("button", { name: "No genre tags (2)" }));
    expect(acts()).toEqual(["Fixture Collective", "Test Pattern Trio"]);
    await user.click(genres.getByRole("button", { name: "Synthetic Wave" }));
    expect(acts()).toHaveLength(3);
  });

  it("offers no capacity band when every act sits in the same one", () => {
    const result = shortlist();
    const shortlistMid = result.shortlist.map((e) => ({ ...e, size_band: "mid" as const }));
    setup({ result: { ...result, shortlist: shortlistMid } });

    expect(filters().queryByRole("group", { name: /Capacity band/ })).toBeNull();
    expect(filters().getByRole("group", { name: /Genre/ })).toBeTruthy();
  });

  it("shows no filters for a run stored before acts carried genres or bands", () => {
    setup({ result: shortlist(plain as RunOut) });

    expect(screen.queryByRole("region", { name: "Filters" })).toBeNull();
    expect(acts()).toHaveLength(3);
  });

  it("filters by genre and band together", () => {
    const list: BrowsableAct[] = [
      { entityId: "a", name: "A", rank: 1, genres: ["X"], band: "mid" },
      { entityId: "b", name: "B", rank: 2, genres: ["X", "Y"], band: "emerging" },
      { entityId: "c", name: "C", rank: 3, genres: [], band: null },
    ];
    expect(filterActs(list, new Set(), null).map((a) => a.entityId)).toEqual(["a", "b", "c"]);
    expect(filterActs(list, new Set(["Y"]), null).map((a) => a.entityId)).toEqual(["b"]);
    expect(filterActs(list, new Set(["X"]), "mid").map((a) => a.entityId)).toEqual(["a"]);
    expect(filterActs(list, new Set(), "established")).toEqual([]);
    expect(filterActs(list, new Set([NO_GENRE]), null).map((a) => a.entityId)).toEqual(["c"]);
  });
});

describe("played here chip", () => {
  it("shows the last night here and its sell-through for an act in past_shows", () => {
    setup({
      played: {
        [HEADLINER]: { date: "2026-03-14", sellThrough: 0.95 },
        [COLLECTIVE]: { date: "2025-06-02", sellThrough: null },
      },
    });

    const [headliner, collective] = screen.getAllByTestId("played-here");
    expect(headliner.textContent).toMatch(/^Played here·14 Mar 2026·95% sold/);
    expect(headliner.querySelector("time")?.getAttribute("dateTime")).toBe("2026-03-14");
    expect(headliner.getAttribute("title")).toMatch(/Outdraw data/);
    // No tickets recorded: the date alone.
    expect(collective.textContent).not.toContain("sold");
    expect(collective.textContent).toContain("2 Jun 2025");
    // An act that never played here has no chip.
    const trio = screen.getByRole("heading", { name: "Test Pattern Trio" }).closest("li")!;
    expect(within(trio).queryByTestId("played-here")).toBeNull();
  });
});

describe("holds calendar", () => {
  it("places the pencilled acts on their nights", async () => {
    const { user } = setup();

    await user.click(screen.getByRole("button", { name: "Calendar" }));

    expect(screen.queryByRole("list", { name: "Ranked acts" })).toBeNull();
    const calendar = within(screen.getByRole("region", { name: "Pencil holds calendar" }));
    expect(calendar.getByRole("heading", { name: "November 2026" })).toBeTruthy();
    const night = document.querySelector('[data-date="2026-11-14"]') as HTMLElement;
    expect(within(night).getByTestId("calendar-hold").textContent).toBe("Synthetic Headliner");
    // The grid's leading and trailing days show their holds too (5 Dec closes November's).
    expect(calendar.getAllByTestId("calendar-hold").map((h) => h.textContent)).toEqual([
      "Synthetic Headliner",
      "Fixture Collective",
    ]);
    // The list under the grid keeps to the month.
    expect(
      within(calendar.getByRole("list", { name: "Holds in November 2026" }))
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["14 Nov 2026Synthetic Headliner"]);
    // A hold without a night is listed under the grid.
    expect(
      within(calendar.getByRole("list", { name: "Pencilled, no date yet" })).getByText(
        "Test Pattern Trio",
      ),
    ).toBeTruthy();

    await user.click(calendar.getByRole("button", { name: "Next month" }));
    expect(calendar.getByRole("heading", { name: "December 2026" })).toBeTruthy();
    const december = document.querySelector('[data-date="2026-12-05"]') as HTMLElement;
    expect(within(december).getByTestId("calendar-hold").textContent).toBe("Fixture Collective");

    await user.click(screen.getByRole("button", { name: "List" }));
    expect(acts()).toHaveLength(3);
  });

  it("shows the venue's holds from its other runs too, each act and night once", async () => {
    const hold = (run_id: string, entity_id: string, name: string, date: string) => ({
      run_id,
      entity_id,
      name,
      date,
    });
    const { user } = setup({
      otherHolds: [
        // The same act and night as this run's hold: shown once.
        hold("run_older", HEADLINER, "Synthetic Headliner", "2026-11-14"),
        // An act this run does not list, held on an earlier run.
        hold("run_older", "urn:entity:synthetic:artist:99", "Earlier Act", "2026-11-20"),
        // This run's own rows come from the page, not the read (a release here must win).
        hold("run_pencilled", COLLECTIVE, "Fixture Collective", "2026-11-21"),
      ],
    });

    await user.click(screen.getByRole("button", { name: "Calendar" }));
    const calendar = within(screen.getByRole("region", { name: "Pencil holds calendar" }));
    const november = within(calendar.getByRole("list", { name: "Holds in November 2026" }));
    expect((await november.findAllByRole("listitem")).map((li) => li.textContent)).toEqual([
      "14 Nov 2026Synthetic Headliner",
      "20 Nov 2026Earlier Act",
    ]);
  });

  it("falls back to this run's holds when the venue read fails", async () => {
    const down = () => Response.json({ reason: "unavailable" }, { status: 503 });
    vi.stubGlobal("fetch", vi.fn(async () => down()));
    const user = userEvent.setup();
    render(
      <ShortlistView
        venueId="ven_fixture"
        venueName="Synthetic Room"
        runId="run_pencilled"
        result={shortlist()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Calendar" }));
    expect((await screen.findByRole("status")).textContent).toContain("other runs");
    expect(screen.getAllByTestId("calendar-hold")).toHaveLength(2);
  });

  it("merges this run's holds over the venue's, one entry per act and night", () => {
    const acts: BrowsableAct[] = [
      { entityId: "a", name: "A", rank: 1, genres: [], band: null },
      { entityId: "b", name: "B", rank: 2, genres: [], band: null },
    ];
    const others = [
      { run_id: "run_1", entity_id: "a", name: "A", date: "2026-11-14" },
      { run_id: "run_0", entity_id: "a", name: "A", date: "2026-11-14" },
      { run_id: "run_0", entity_id: "c", name: "C", date: null },
      { run_id: "run_0", entity_id: "b", name: "B", date: "2026-11-01" },
    ];
    const merged = mergeHolds({ a: "2026-11-14", b: null }, acts, others, "run_1");
    expect(merged.map((h) => [h.entityId, h.date])).toEqual([
      ["b", null],
      ["c", null],
      ["b", "2026-11-01"],
      ["a", "2026-11-14"],
    ]);
  });

  it("says how to pencil a hold when there is none", () => {
    holdsApi();
    render(
      <HoldsProvider runId="run_x" initial={{}}>
        <HoldsCalendar venueId="ven_x" runId="run_x" acts={[]} />
      </HoldsProvider>,
    );
    expect(screen.getByText(/No pencil holds yet/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "October 2026" })).toBeTruthy();
  });

  it("says holds are off on a demo run", () => {
    holdsApi();
    render(
      <PrecomputedProvider value={{ runId: "run_x", soundcheck: {}, briefs: {}, compare: null }}>
        <HoldsProvider runId="run_x" initial={{}}>
          <HoldsCalendar venueId="ven_x" runId="run_x" acts={[]} />
        </HoldsProvider>
      </PrecomputedProvider>,
    );
    expect(screen.getByText(/Pencil holds are off on the demo/)).toBeTruthy();
  });

  it("shows a hold as soon as the pencil switch stores it", async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/holds")) return Response.json({ venue_id: "ven_x", holds: [] });
      const body = JSON.parse(String(init?.body)) as { pencilled: boolean; date?: string };
      return Response.json({ run_id: "run_x", entity_id: "a", ...body, date: body.date ?? null });
    });
    vi.stubGlobal("fetch", fetch);
    const user = userEvent.setup();
    const act: BrowsableAct = {
      entityId: "a",
      name: "Synthetic Act",
      rank: 1,
      genres: [],
      band: null,
    };
    render(
      <HoldsProvider runId="run_x" initial={{}}>
        <PencilToggle runId="run_x" entityId="a" initial={false} />
        <HoldsCalendar venueId="ven_x" runId="run_x" acts={[act]} />
      </HoldsProvider>,
    );

    await user.click(screen.getByRole("switch", { name: "Pencil hold" }));
    expect(await screen.findByRole("list", { name: "Pencilled, no date yet" })).toBeTruthy();

    // Typing saves nothing until the field is left (one PATCH so far, plus the holds read).
    await user.type(screen.getByLabelText("Hold date"), "2026-10-30");
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);
    await user.tab();
    const night = document.querySelector('[data-date="2026-10-30"]') as HTMLElement;
    expect(await within(night).findByTestId("calendar-hold")).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls.at(-1)?.[1]?.body))).toEqual({
      pencilled: true,
      date: "2026-10-30",
    });
  });
});
