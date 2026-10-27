// @vitest-environment jsdom
// The server component against a fake agent: the same fetch the live backend answers.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { agent } from "@/src/server/agent";
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

  it("maps an agent outage to the user-facing reason, with a reload and a way back", async () => {
    fakeAgent(new TypeError("fetch failed"));
    await renderPage();

    expect(screen.getByRole("alert").textContent).toContain("Can't reach Outdraw right now");
    expect(screen.getByRole("link", { name: "Try again" }).getAttribute("href")).toBe(
      "/venue/ven_fixture/shortlist",
    );
    expect(screen.getByRole("link", { name: "Back to crowd profile" }).getAttribute("href")).toBe(
      "/venue/ven_fixture",
    );
  });

  it("lists the matches still to confirm on the agent's 409", async () => {
    fakeAgent(
      Response.json(venue),
      Response.json({ detail: { pending: ["Synthetic Act"] } }, { status: 409 }),
    );
    await renderPage("ven_fixture", { run: "run_fixture" });

    expect(screen.getByRole("alert").getAttribute("data-reason")).toBe("conflict");
    expect(screen.getByRole("list", { name: "Still to confirm" }).textContent).toBe("Synthetic Act");
  });

  it("lets anything but an AgentError reach the page's error boundary", async () => {
    vi.spyOn(agent, "getVenue").mockRejectedValue(new TypeError("a bug of ours"));

    await expect(ShortlistPage(props("ven_fixture"))).rejects.toThrow("a bug of ours");
  });
});
