// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ResolutionItem, Resolutions } from "@/src/lib/api-client";
import { ConfirmMatches } from "./confirm-matches";
import fixture from "./fixtures/resolve.json";
import { fakeApi, type Call } from "./test-utils";

const resolutions = fixture as Resolutions;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Answer /confirm like the agent: the item with the pick recorded and confirmed. */
function confirmed(call: Call): Response {
  const { query, chosen_entity_id } = call.body as { query: string; chosen_entity_id: string | null };
  const item = resolutions.items.find((i) => i.query === query) as ResolutionItem;
  const chosen = item.candidates.find((c) => c.entity_id === chosen_entity_id);
  return Response.json({
    ...item,
    confirmed: true,
    entity_id: chosen?.entity_id ?? null,
    canonical_name: chosen?.name ?? item.canonical_name,
  });
}

function setup(items = resolutions.items) {
  const onDone = vi.fn();
  render(
    <ConfirmMatches
      venueId="ven_fixture"
      venueName="Synthetic Room"
      items={items}
      onBack={() => {}}
      onDone={onDone}
    />,
  );
  return { user: userEvent.setup(), onDone };
}

const build = () => screen.getByRole("button", { name: "Build crowd profile" }) as HTMLButtonElement;
const group = (name: string) => screen.getByRole("group", { name: new RegExp(name) });

describe("confirm matches", () => {
  it("renders auto-confirmed matches and candidates from the resolve response", () => {
    fakeApi([]);
    setup();

    expect(screen.getByText("Qloo match")).toBeTruthy();
    expect(screen.getAllByTestId("match-row")).toHaveLength(4);
    expect(screen.getByText(/Synthetic Room NYC/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change match for Synthetic Act" })).toBeTruthy();

    const ambiguous = within(group("Ambiguous Band"));
    expect(ambiguous.getAllByRole("radio").map((r) => r.closest("label")?.textContent)).toEqual([
      "Ambiguous Band · artist(best guess)",
      "Ambiguous Band (UK) · artist",
      "The Ambiguous Band · artist",
      "Not in Qloo: skip it",
    ]);
    // A best guess is not a choice: nothing is pre-selected.
    expect(ambiguous.getAllByRole("radio").some((r) => (r as HTMLInputElement).checked)).toBe(false);

    const unknown = within(group("Unknown Duo"));
    expect(unknown.getByText(/Qloo found no match/)).toBeTruthy();
    expect(unknown.getAllByRole("radio")).toHaveLength(1);
  });

  it("blocks the profile call until every row is confirmed", async () => {
    const calls = fakeApi([
      [/\/confirm$/, confirmed],
      [/\/confirm$/, confirmed],
      [/\/profile$/, Response.json({})],
    ]);
    const { user, onDone } = setup();

    expect(build().disabled).toBe(true);
    expect(screen.getByText("2 matches left to confirm")).toBeTruthy();
    await user.click(build());
    expect(calls).toEqual([]);

    await user.click(within(group("Ambiguous Band")).getByLabelText(/Ambiguous Band \(UK\)/));
    await screen.findByText("1 match left to confirm");
    expect(build().disabled).toBe(true);

    await user.click(within(group("Unknown Duo")).getByLabelText(/Not in Qloo/));
    await screen.findByText("All matches confirmed");
    expect(screen.getByText(/Ambiguous Band \(UK\)/)).toBeTruthy();
    expect(screen.getByText("Skipped: not in Qloo")).toBeTruthy();
    expect(build().disabled).toBe(false);

    await user.click(build());
    expect(calls.map((c) => [c.url, c.body])).toEqual([
      [
        "/api/venues/ven_fixture/confirm",
        { query: "Ambiguous Band", chosen_entity_id: "urn:entity:synthetic:artist:3" },
      ],
      ["/api/venues/ven_fixture/confirm", { query: "Unknown Duo", chosen_entity_id: null }],
      ["/api/venues/ven_fixture/profile", undefined],
    ]);
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("lets the user change an auto-confirmed match, blocking the profile meanwhile", async () => {
    const calls = fakeApi([[/\/confirm$/, confirmed]]);
    const auto = resolutions.items.slice(0, 2);
    const { user } = setup(auto);

    expect(build().disabled).toBe(false);
    await user.click(screen.getByRole("button", { name: "Change match for Synthetic Act" }));
    expect(build().disabled).toBe(true);
    const radios = within(group("Synthetic Act")).getAllByRole("radio") as HTMLInputElement[];
    expect(radios[0].checked).toBe(true);

    await user.click(within(group("Synthetic Act")).getByLabelText(/Not in Qloo/));
    await screen.findByText("Skipped: not in Qloo");
    expect(build().disabled).toBe(false);
    expect(calls[0].body).toEqual({ query: "Synthetic Act", chosen_entity_id: null });
  });

  it("reopens rows the agent reports as pending", async () => {
    fakeApi([
      [
        /\/profile$/,
        Response.json({ reason: "conflict", pending: ["Synthetic Act"] }, { status: 409 }),
      ],
    ]);
    const { user, onDone } = setup(resolutions.items.slice(0, 2));

    await user.click(build());

    expect((await screen.findByRole("alert")).textContent).toMatch(/Confirm every Qloo match/);
    expect(group("Synthetic Act")).toBeTruthy();
    expect(build().disabled).toBe(true);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("collapses a row on pick and waits for the save before building", async () => {
    let release: (() => void) | undefined;
    const slow = new Promise<void>((r) => (release = r));
    const calls = fakeApi([[/\/confirm$/, (call) => slow.then(() => confirmed(call))]]);
    const { user } = setup(resolutions.items.slice(0, 3));

    await user.click(within(group("Ambiguous Band")).getByLabelText(/Ambiguous Band \(UK\)/));

    // Optimistic: the row is collapsed and the other rows stay usable.
    expect(screen.getByText(/Ambiguous Band \(UK\)/)).toBeTruthy();
    expect(screen.queryByRole("group", { name: /Ambiguous Band/ })).toBeNull();
    expect(screen.getByText("Saving your picks…")).toBeTruthy();
    expect(build().disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Change match for Synthetic Act" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);

    release?.();
    await screen.findByText("All matches confirmed");
    expect(build().disabled).toBe(false);
    expect(calls).toHaveLength(1);
  });

  it("reverts the row with an error when saving a pick fails", async () => {
    fakeApi([[/\/confirm$/, Response.json({ reason: "unavailable" }, { status: 503 })]]);
    const { user } = setup();

    await user.click(within(group("Unknown Duo")).getByLabelText(/Not in Qloo/));

    expect((await screen.findByRole("alert")).textContent).toMatch(/Can't reach Outdraw/);
    expect(group("Unknown Duo")).toBeTruthy();
    expect(screen.queryByText("Skipped: not in Qloo")).toBeNull();
    expect(screen.getByText("2 matches left to confirm")).toBeTruthy();
    expect(build().disabled).toBe(true);
  });
});
