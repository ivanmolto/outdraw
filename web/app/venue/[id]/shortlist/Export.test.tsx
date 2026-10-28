// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlanLoader, resetPlan } from "@/src/components/pro";
import { parseRun } from "@/src/lib/run-result";
import type { RunOut } from "@/types/agent";
import fixture from "./fixtures/pencilled-run.json";
import { ShortlistView } from "./shortlist-view";

function setup(features: Record<string, boolean> | null = null) {
  const parsed = parseRun(fixture as RunOut);
  if (parsed.kind !== "shortlist") throw new Error("fixture should parse as a shortlist");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/me" && features
        ? Response.json({ plan: "free", features })
        : url.endsWith("/holds")
          ? Response.json({ venue_id: "ven_fixture", holds: [] })
          : Response.json({ reason: "not_found" }, { status: 404 }),
    ),
  );
  const blobs: Blob[] = [];
  // jsdom has no object URLs; this file's environment is its own, so no restore.
  Object.assign(URL, {
    createObjectURL: (b: Blob) => {
      blobs.push(b);
      return "blob:export";
    },
    revokeObjectURL: () => {},
  });
  const names: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    names.push(this.download);
  });
  render(
    <>
      <PlanLoader />
      <ShortlistView
        venueId="ven_fixture"
        venueName="Synthetic Room"
        runId={parsed.runId}
        result={parsed}
      />
    </>,
  );
  return { user: userEvent.setup(), blobs, names };
}

beforeEach(() => {
  resetPlan();
  vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-28T12:00:00Z") });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Export to my booking tool", () => {
  it("downloads the shortlist as a CSV with its pencilled dates", async () => {
    const { user, blobs, names } = setup();

    await user.click(screen.getByText("Export to my booking tool"));
    expect(screen.getByText(/not a live integration/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Download shortlist (CSV)" }));

    expect(names).toEqual(["outdraw-synthetic-room-shortlist-2026-10-28.csv"]);
    expect(blobs[0].type).toBe("text/csv;charset=utf-8");
    const lines = (await blobs[0].text()).replace(/^﻿/, "").split("\r\n");
    expect(lines[0]).toBe("act,qloo_id,score,top_reason,pencilled_date,support_act");
    expect(lines[1]).toMatch(/^Synthetic Headliner,urn:entity:synthetic:artist:11,0\.8125,/);
    expect(lines[1]).toMatch(/,2026-11-14,$/);
    expect(lines[2]).toMatch(/,2026-12-05,$/);
    expect(lines[3]).toMatch(/,TBD,$/);
  });

  it("downloads the pencil-hold summary as plain text", async () => {
    const { user, blobs, names } = setup();

    await user.click(screen.getByText("Export to my booking tool"));
    await user.click(
      screen.getByRole("button", { name: "Download pencil-hold summary (text)" }),
    );

    expect(names).toEqual(["outdraw-synthetic-room-holds-2026-10-28.txt"]);
    const text = await blobs[0].text();
    expect(text).toContain("Pencil holds: Synthetic Room");
    expect(text).toContain("Sat, 14 Nov 2026: Synthetic Headliner");
    expect(text).toContain("Night to be set: Test Pattern Trio");
  });

  it("closes after a download, on a click outside and on Escape", async () => {
    const { user } = setup();
    const menu = () => screen.getByText("Export to my booking tool").closest("details")!;

    await user.click(screen.getByText("Export to my booking tool"));
    expect(menu().open).toBe(true);
    await user.click(screen.getByRole("button", { name: "Download shortlist (CSV)" }));
    expect(menu().open).toBe(false);

    await user.click(screen.getByText("Export to my booking tool"));
    await user.click(screen.getByRole("heading", { name: "Synthetic Room" }));
    expect(menu().open).toBe(false);

    await user.click(screen.getByText("Export to my booking tool"));
    await user.keyboard("{Escape}");
    expect(menu().open).toBe(false);
    expect(document.activeElement).toBe(menu().querySelector("summary"));
  });

  it("is Pro: a free plan sees the badge and no export", async () => {
    setup({ exports: false, full_shortlist: false });

    expect(await screen.findByText(/Exports to your booking tool: a Pro feature/)).toBeTruthy();
    expect(screen.queryByText("Export to my booking tool")).toBeNull();
  });
});
