// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { defaultConstraints } from "@/src/lib/run-result";
import { fakeApi } from "../../new/test-utils";
import fixture from "./fixtures/run.json";
import { RunShortlist } from "./run-shortlist";

const constraints = defaultConstraints(400);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function setup() {
  render(
    <StrictMode>
      <RunShortlist venueId="ven_fixture" venueName="Synthetic Room" constraints={constraints} />
    </StrictMode>,
  );
}

describe("fresh run", () => {
  it("posts one run with the default constraints, shows a skeleton, then the list", async () => {
    let answer!: (r: Response) => void;
    const calls = fakeApi([[/\/api\/runs$/, () => new Promise<Response>((r) => (answer = r))]]);
    setup();

    expect(screen.getByRole("status", { name: "Building shortlist" })).toBeTruthy();
    answer(Response.json(fixture));

    expect(await screen.findByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(calls).toEqual([
      {
        method: "POST",
        url: "/api/runs",
        body: { venue_id: "ven_fixture", constraints, message: "" },
      },
    ]);
    expect(window.location.search).toBe("?run=run_fixture");
  });

  it("maps a {reason} to its message and retries when that can help", async () => {
    const calls = fakeApi([
      [/\/api\/runs$/, Response.json({ reason: "busy" }, { status: 409 })],
      [/\/api\/runs$/, Response.json(fixture)],
    ]);
    setup();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Outdraw is busy with another request");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(calls).toHaveLength(2);
  });

  it("offers no retry when the budget is spent", async () => {
    fakeApi([[/\/api\/runs$/, Response.json({ reason: "budget" }, { status: 429 })]]);
    setup();

    expect((await screen.findByRole("alert")).textContent).toContain("today's Qloo budget");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByRole("link", { name: "Back to crowd profile" })).toBeTruthy();
  });
});
