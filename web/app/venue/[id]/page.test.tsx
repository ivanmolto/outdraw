// @vitest-environment jsdom
// The server component against a fake agent: the same fetch the live backend answers.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_URL, fakeAgent, KEY } from "@/src/server/test-agent";
import fixture from "./fixtures/venue.json";
import VenueNotFound from "./not-found";
import VenuePage from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const props = (id: string) => ({ params: Promise.resolve({ id }) }) as PageProps<"/venue/[id]">;

async function renderPage(id = "ven_fixture") {
  render(await VenuePage(props(id)));
}

describe("/venue/[id]", () => {
  it("fetches the venue from the agent and renders its profile", async () => {
    const sent = fakeAgent(Response.json(fixture));
    await renderPage();

    expect(sent).toEqual([
      { method: "GET", url: `${AGENT_URL}/venues/ven_fixture`, key: KEY, body: undefined },
    ]);
    expect(screen.getByRole("heading", { level: 1, name: "Synthetic Room" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Genres" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Build shortlist" })).toBeTruthy();
  });

  it("asks for confirmations when the venue has no profile yet", async () => {
    fakeAgent(Response.json({ ...fixture, profile: null }));
    await renderPage();

    expect(screen.getByRole("alert").textContent).toContain("No crowd profile yet");
    expect(screen.queryByRole("link", { name: "Build shortlist" })).toBeNull();
  });

  it("is a 404 for an unknown venue", async () => {
    fakeAgent(Response.json({ detail: "not found" }, { status: 404 }));

    await expect(VenuePage(props("ven_missing"))).rejects.toMatchObject({
      digest: expect.stringContaining("404"),
    });
  });

  it("is a 404 for a malformed id, without calling the agent", async () => {
    const sent = fakeAgent();

    await expect(VenuePage(props("not a valid id!"))).rejects.toMatchObject({
      digest: expect.stringContaining("404"),
    });
    expect(sent).toEqual([]);
  });

  it("has a branded not-found page with a way back", () => {
    render(<VenueNotFound />);

    expect(screen.getByRole("heading", { level: 1, name: "Venue not found" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Set up a venue" }).getAttribute("href")).toBe(
      "/venue/new",
    );
  });

  it("maps an agent outage to the user-facing reason", async () => {
    fakeAgent(new TypeError("fetch failed"));
    await renderPage();

    expect(screen.getByRole("alert").textContent).toContain("Can't reach Outdraw right now");
  });
});
