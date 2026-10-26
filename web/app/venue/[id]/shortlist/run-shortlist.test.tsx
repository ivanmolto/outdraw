// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { defaultConstraints } from "@/src/lib/run-result";
import { fakeApi } from "../../new/test-utils";
import fixture from "./fixtures/run.json";
import { seedSample } from "./fixtures/sample";
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
    expect(screen.getByRole("timer", { name: "Time elapsed" }).textContent).toBe("0:00 elapsed");
    answer(Response.json(fixture));

    expect(await screen.findByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
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
      [/\/api\/runs$/, Response.json({ reason: "unavailable" }, { status: 503 })],
      [/\/api\/runs$/, Response.json(fixture)],
    ]);
    setup();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Can't reach Outdraw right now");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(calls).toHaveLength(2);
  });

  it("offers no retry for input the agent rejects", async () => {
    fakeApi([[/\/api\/runs$/, Response.json({ reason: "conflict" }, { status: 409 })]]);
    setup();

    expect((await screen.findByRole("alert")).textContent).toContain("Confirm every Qloo match");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByRole("link", { name: "Back to crowd profile" })).toBeTruthy();
  });
});

describe("refused run", () => {
  it.each([
    [429, "busy"],
    [429, "budget"],
    [503, "live_mode_off"],
  ])("shows the named seed as a sample on %i %s, with no error", async (status, reason) => {
    const calls = fakeApi([
      [/\/api\/runs$/, Response.json({ reason, fallback_seed: "brooklyn-indie" }, { status })],
      [/\/api\/seeds\/brooklyn-indie$/, Response.json(seedSample("brooklyn-indie"))],
    ]);
    setup();

    const banner = await screen.findByRole("status", { name: "Sample shortlist" });
    expect(banner.textContent).toContain("Live analysis is busy, here's a sample");
    expect(screen.getByRole("heading", { name: "Synthetic Seed brooklyn-indie" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "Ranked acts" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Refine this shortlist")).toBeNull();
    expect(screen.getByRole("link", { name: "Open the full demo" }).getAttribute("href")).toBe(
      "/v/brooklyn-indie",
    );
    expect(window.location.search).toBe("");
    expect(calls.map((c) => c.url)).toEqual(["/api/runs", "/api/seeds/brooklyn-indie"]);
  });

  it("falls back to the default seed when none is named, and retries live", async () => {
    const calls = fakeApi([
      [/\/api\/runs$/, Response.json({ reason: "busy" }, { status: 429 })],
      [/\/api\/seeds\/nola-jazz$/, Response.json(seedSample("nola-jazz"))],
      [/\/api\/runs$/, Response.json(fixture)],
    ]);
    setup();

    expect(await screen.findByRole("status", { name: "Sample shortlist" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try a live run again" }));

    expect(await screen.findByRole("heading", { name: "Synthetic Room" })).toBeTruthy();
    expect(screen.queryByRole("status", { name: "Sample shortlist" })).toBeNull();
    expect(calls.map((c) => c.url)).toEqual(["/api/runs", "/api/seeds/nola-jazz", "/api/runs"]);
  });

  it("ignores a fallback_seed that is no seed", async () => {
    const calls = fakeApi([
      [/\/api\/runs$/, Response.json({ reason: "live_mode_off", fallback_seed: "../x" }, { status: 503 })],
      [/\/api\/seeds\/nola-jazz$/, Response.json(seedSample("nola-jazz"))],
    ]);
    setup();

    expect(await screen.findByRole("status", { name: "Sample shortlist" })).toBeTruthy();
    expect(calls[1].url).toBe("/api/seeds/nola-jazz");
  });

  it("shows the busy message when the sample can't be read either", async () => {
    fakeApi([
      [/\/api\/runs$/, Response.json({ reason: "live_mode_off" }, { status: 503 })],
      [/\/api\/seeds\/nola-jazz$/, Response.json({ reason: "unavailable" }, { status: 503 })],
    ]);
    setup();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Outdraw is busy with another request");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
