// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import resolveFixture from "./fixtures/resolve.json";
import { DRAFT_KEY, NewVenue } from "./new-venue";
import { fakeApi } from "./test-utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => {
  push.mockReset();
  window.scrollTo = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

const type = (label: string | RegExp, value: string, scope: HTMLElement = document.body) =>
  fireEvent.change(within(scope).getByLabelText(label), { target: { value } });

function fillVenue() {
  type("Venue name", "Synthetic Room");
  type("City", "New York");
  type("Country", "US");
  type(/^Capacity$/, "300");
}

const show = (n: number) => screen.getByRole("group", { name: `Show ${n}` });

describe("venue form", () => {
  it("shows an empty state until a show is added", async () => {
    const user = userEvent.setup();
    render(<NewVenue />);

    expect(screen.getByText("No past shows yet")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Add a past show" }));

    expect(screen.queryByText("No past shows yet")).toBeNull();
    expect(show(1)).toBeTruthy();
  });

  it("shows validation messages and does not call the API", async () => {
    const user = userEvent.setup();
    const calls = fakeApi([]);
    render(<NewVenue />);

    type("City", "New York");
    type(/^Capacity$/, "lots");
    await user.click(screen.getByRole("button", { name: "Add a past show" }));
    type("Tickets sold", "12.5", show(1));
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));

    const summary = screen.getByRole("alert");
    expect(summary.textContent).toContain("6 fields need attention");
    expect(screen.getByLabelText("Venue name").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getAllByText("Required")).toHaveLength(3); // name, country, act
    expect(screen.getByText("Enter a number", { selector: "#f-capacity-error" })).toBeTruthy();
    expect(within(show(1)).getByText("Pick a date")).toBeTruthy();
    expect(within(show(1)).getByText("Enter a whole number")).toBeTruthy();
    expect(document.activeElement?.id).toBe("f-name");
    expect(calls).toEqual([]);

    // Messages clear keystroke by keystroke as the user fixes fields.
    await user.type(screen.getByLabelText("Venue name"), "S");
    expect(screen.getByLabelText("Venue name").getAttribute("aria-invalid")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("5 fields need attention");
    const capacity = document.getElementById("f-capacity") as HTMLInputElement;
    await user.clear(capacity);
    await user.type(capacity, "300");
    expect(screen.getByRole("alert").textContent).toContain("4 fields need attention");
  });

  it("keeps the draft and venue id in sessionStorage across a reload", async () => {
    const user = userEvent.setup();
    fakeApi([
      [/\/api\/venues$/, Response.json({ id: "ven_fixture" }, { status: 201 })],
      [/\/resolve$/, Response.json({ reason: "unavailable" }, { status: 503 })],
    ]);
    const first = render(<NewVenue />);

    fillVenue();
    await user.click(screen.getByRole("button", { name: "Add a past show" }));
    type("Act", "Synthetic Act", show(1));
    type("Date", "2026-05-01", show(1));
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Can't reach Outdraw/);
    first.unmount();

    const stored = JSON.parse(window.sessionStorage.getItem(DRAFT_KEY) ?? "null");
    expect(stored).toMatchObject({
      venueId: "ven_fixture",
      draft: { name: "Synthetic Room", past_shows: [{ act: "Synthetic Act" }] },
    });

    const calls = fakeApi([
      [/\/api\/venues$/, Response.json({ id: "ven_fixture" }, { status: 201 })],
      [/\/resolve$/, Response.json(resolveFixture)],
    ]);
    render(<NewVenue />);
    expect((await screen.findByDisplayValue("Synthetic Room")).id).toBe("f-name");
    expect((within(show(1)).getByLabelText("Act") as HTMLInputElement).value).toBe("Synthetic Act");

    // The restored id means a re-save updates the same venue.
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));
    await screen.findByRole("heading", { name: "Confirm matches" });
    expect(calls[0].body).toMatchObject({ id: "ven_fixture" });
  });

  it("ignores a corrupt stored draft", async () => {
    window.sessionStorage.setItem(DRAFT_KEY, "{not json");
    render(<NewVenue />);
    expect(await screen.findByText("No past shows yet")).toBeTruthy();
  });

  it("adds and removes show rows", async () => {
    const user = userEvent.setup();
    render(<NewVenue />);

    await user.click(screen.getByRole("button", { name: "Add a past show" }));
    await user.click(screen.getByRole("button", { name: "Add another show" }));
    await user.click(screen.getByRole("button", { name: "Add another show" }));
    type("Act", "First", show(1));
    type("Act", "Second", show(2));
    type("Act", "Third", show(3));
    expect(screen.getByText("3 of 10")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Remove show 2" }));

    expect(screen.getByText("2 of 10")).toBeTruthy();
    expect((within(show(1)).getByLabelText("Act") as HTMLInputElement).value).toBe("First");
    expect((within(show(2)).getByLabelText("Act") as HTMLInputElement).value).toBe("Third");

    await user.click(screen.getByRole("button", { name: "Remove show 2" }));
    await user.click(screen.getByRole("button", { name: "Remove show 1" }));
    expect(screen.getByText("No past shows yet")).toBeTruthy();
  });

  it("allows at most 10 show rows", async () => {
    const user = userEvent.setup();
    render(<NewVenue />);

    await user.click(screen.getByRole("button", { name: "Add a past show" }));
    const add = screen.getByRole("button", { name: "Add another show" });
    for (let i = 0; i < 12; i++) await user.click(add);

    expect(screen.getAllByRole("group", { name: /^Show \d+$/ })).toHaveLength(10);
    expect(screen.getByText("10 of 10")).toBeTruthy();
    expect((add as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/maximum of 10/)).toBeTruthy();
  });

  it("saves, resolves and shows the Qloo match step", async () => {
    const user = userEvent.setup();
    const calls = fakeApi([
      [/\/api\/venues$/, Response.json({ id: "ven_fixture" }, { status: 201 })],
      [/\/resolve$/, Response.json(resolveFixture)],
    ]);
    render(<NewVenue />);

    fillVenue();
    await user.click(screen.getByRole("button", { name: "Add a past show" }));
    type("Act", "Synthetic Act", show(1));
    type("Date", "2026-05-01", show(1));
    type("Tickets sold", "280", show(1));
    type("Ticket price", "25.50", show(1));
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));

    expect(await screen.findByRole("heading", { name: "Confirm matches" })).toBeTruthy();
    expect(screen.getByText("Qloo match")).toBeTruthy();
    expect(calls.map((c) => [c.method, c.url])).toEqual([
      ["POST", "/api/venues"],
      ["POST", "/api/venues/ven_fixture/resolve"],
    ]);
    expect(calls[0].body).toEqual({
      name: "Synthetic Room",
      city: "New York",
      country: "US",
      capacity: 300,
      past_shows: [{ act: "Synthetic Act", date: "2026-05-01", tickets_sold: 280, ticket_price: 25.5 }],
    });
  });

  it("re-saves the same venue after going back to edit", async () => {
    const user = userEvent.setup();
    const calls = fakeApi([
      [/\/api\/venues$/, Response.json({ id: "ven_fixture" }, { status: 201 })],
      [/\/resolve$/, Response.json(resolveFixture)],
      [/\/api\/venues$/, Response.json({ id: "ven_fixture" }, { status: 201 })],
      [/\/resolve$/, Response.json(resolveFixture)],
    ]);
    render(<NewVenue />);

    fillVenue();
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));
    await user.click(await screen.findByRole("button", { name: "Back to edit" }));
    expect((screen.getByLabelText("Venue name") as HTMLInputElement).value).toBe("Synthetic Room");
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));

    await screen.findByRole("heading", { name: "Confirm matches" });
    expect(calls[2].body).toMatchObject({ id: "ven_fixture", name: "Synthetic Room" });
  });

  it("shows the API's reason when saving fails", async () => {
    const user = userEvent.setup();
    fakeApi([[/\/api\/venues$/, Response.json({ reason: "busy" }, { status: 429 })]]);
    render(<NewVenue />);

    fillVenue();
    await user.click(screen.getByRole("button", { name: "Save and match acts" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/busy/);
    expect(screen.queryByRole("heading", { name: "Confirm matches" })).toBeNull();
  });
});
