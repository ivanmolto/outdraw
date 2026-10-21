// @vitest-environment jsdom
// The server component against a fake agent: the same fetch the live backend answers.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_URL, fakeAgent, KEY } from "@/src/server/test-agent";
import venue from "../fixtures/venue.json";
import run from "./fixtures/run.json";
import ShortlistPage from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const props = (id: string, search: Record<string, string> = {}) =>
  ({
    params: Promise.resolve({ id }),
    searchParams: Promise.resolve(search),
  }) as PageProps<"/venue/[id]/shortlist">;

async function renderPage(id = "ven_fixture", search?: Record<string, string>) {
  render(await ShortlistPage(props(id, search)));
}

describe("/venue/[id]/shortlist", () => {
  it("starts a fresh run with the venue's default constraints", async () => {
    // The client component posts on mount; fetch is the fake agent, so stop it there.
    const sent = fakeAgent(Response.json(venue), new TypeError("no run in this test"));
    await renderPage();

    expect(sent[0]).toEqual({
      method: "GET",
      url: `${AGENT_URL}/venues/ven_fixture`,
      key: KEY,
      body: undefined,
    });
    expect(screen.getByRole("status", { name: "Building shortlist" })).toBeTruthy();
  });

  it("reads a stored run from ?run= without starting a new one", async () => {
    const sent = fakeAgent(Response.json(venue), Response.json(run));
    await renderPage("ven_fixture", { run: "run_fixture" });

    expect(sent.map((s) => `${s.method} ${s.url}`)).toEqual([
      `GET ${AGENT_URL}/venues/ven_fixture`,
      `GET ${AGENT_URL}/runs/run_fixture`,
    ]);
    expect(screen.getByRole("list", { name: "Ranked acts" })).toBeTruthy();
  });

  it("ignores a ?run= that belongs to another venue", async () => {
    fakeAgent(Response.json(venue), Response.json({ ...run, venue_id: "ven_other" }));
    await renderPage("ven_fixture", { run: "run_fixture" });

    expect(screen.queryByRole("list", { name: "Ranked acts" })).toBeNull();
    expect(screen.getByRole("status", { name: "Building shortlist" })).toBeTruthy();
  });

  it("asks for the profile first when the venue has none", async () => {
    fakeAgent(Response.json({ ...venue, profile: null }));
    await renderPage();

    expect(screen.getByRole("alert").textContent).toContain("No crowd profile yet");
  });

  it("is a 404 for an unknown venue", async () => {
    fakeAgent(Response.json({ detail: "not found" }, { status: 404 }));

    await expect(ShortlistPage(props("ven_missing"))).rejects.toMatchObject({
      digest: expect.stringContaining("404"),
    });
  });

  it("maps an agent outage to the user-facing reason", async () => {
    fakeAgent(new TypeError("fetch failed"));
    await renderPage();

    expect(screen.getByRole("alert").textContent).toContain("Can't reach Outdraw right now");
  });
});
