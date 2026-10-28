// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HACKATHON_BANNER, me } from "@/src/lib/entitlements";
import { HackathonBanner, HackathonPill } from "./hackathon-banner";
import { PlanLoader, ProBadge, ProGate, resetPlan } from "./pro";

afterEach(() => {
  cleanup();
  resetPlan();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function serve(body: unknown, status = 200) {
  const fetch = vi.fn(async () => Response.json(body, { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function gated() {
  render(
    <>
      <p>Top five</p>
      <ProGate feature="full_shortlist" fallback={<p>Upgrade for more</p>}>
        <p>Act six</p>
      </ProGate>
      <PlanLoader />
    </>,
  );
}

describe("ProGate", () => {
  it("hides a Pro feature from a free plan once /api/me answers", async () => {
    const fetch = serve(me("free", false, 10, 10, 3));
    gated();
    expect(await screen.findByText("Upgrade for more")).toBeTruthy();
    expect(screen.queryByText("Act six")).toBeNull();
    expect(screen.getByText("Top five")).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith("/api/me", { cache: "no-store" });
  });

  it("shows it to everyone in hackathon mode", async () => {
    const fetch = serve(me("free", true, 10, 10, 30));
    gated();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.getByText("Act six")).toBeTruthy();
    expect(screen.queryByText("Upgrade for more")).toBeNull();
  });

  it("shows it when /api/me fails: nothing blocks on the read", async () => {
    const fetch = serve({ reason: "unavailable" }, 503);
    gated();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.getByText("Act six")).toBeTruthy();
  });

  it("falls back to the feature's note by default", async () => {
    const fetch = serve(me("free", false, 10, 10, 3));
    render(
      <>
        <ProGate feature="briefs">
          <p>Brief</p>
        </ProGate>
        <PlanLoader />
      </>,
    );
    expect((await screen.findByRole("note")).textContent).toContain("Booking briefs and outreach");
    expect(fetch).toHaveBeenCalled();
    expect(screen.queryByText("Brief")).toBeNull();
  });

  it("shows it without a loader", () => {
    render(
      <ProGate feature="briefs">
        <p>Brief</p>
      </ProGate>,
    );
    expect(screen.getByText("Brief")).toBeTruthy();
  });
});

describe("ProBadge", () => {
  it("reads Pro, or stays out of a control's name when decorative", () => {
    render(
      <>
        <ProBadge />
        <button type="button">
          Brief <ProBadge decorative />
        </button>
      </>,
    );
    const badges = screen.getAllByTitle("A Pro feature");
    expect(badges.map((b) => b.textContent)).toEqual(["Pro", "Pro"]);
    expect(screen.getByRole("button", { name: "Brief" })).toBeTruthy();
  });
});

describe("HackathonBanner", () => {
  it("says every Pro feature is free while on, and is absent while off", () => {
    const { rerender } = render(<HackathonBanner on />);
    expect(screen.getByRole("note").textContent).toContain(HACKATHON_BANNER);
    rerender(<HackathonBanner on={false} />);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("reads HACKATHON_MODE per request", async () => {
    vi.stubEnv("HACKATHON_MODE", "true");
    render(await HackathonPill());
    expect(screen.getByRole("link", { name: /Qloo hackathon/ }).getAttribute("href")).toBe(
      "/pricing",
    );
    cleanup();
    vi.stubEnv("HACKATHON_MODE", "false");
    render(await HackathonPill());
    expect(screen.queryByRole("note")).toBeNull();
  });
});
