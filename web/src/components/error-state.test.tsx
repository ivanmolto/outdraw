// @vitest-environment jsdom
// Every API `{reason}` as the booker sees it: a clear message and a next action, each.
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Reason } from "@/types/agent";
import { ErrorState } from "./error-state";

afterEach(cleanup);

const BACKEND = /8787|render\.com|http:|Traceback|at \w+ \(/;

describe("ErrorState", () => {
  it.each<[Reason, string, string]>([
    ["busy", "Outdraw is busy with another request", "See a sample shortlist"],
    ["budget", "used today's Qloo budget", "See a sample shortlist"],
    ["live_mode_off", "switched off right now", "See a sample shortlist"],
    ["invalid_input", "Some details were rejected", "Edit the venue"],
    ["not_found", "That venue no longer exists", "Set up a venue"],
    ["conflict", "Confirm every Qloo match", "Confirm the matches"],
    ["unavailable", "Can't reach Outdraw right now", "Back to the start"],
    ["unknown", "Something went wrong on our side", "Back to the start"],
  ])("%s: a message and a next action", (reason, message, link) => {
    render(<ErrorState reason={reason} />);

    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("data-reason")).toBe(reason);
    expect(within(alert).getByRole("heading", { level: 1 }).textContent?.length).toBeGreaterThan(0);
    expect(alert.textContent).toContain(message);
    expect(within(alert).getByRole("link", { name: link })).toBeTruthy();
    expect(alert.textContent).not.toMatch(BACKEND);
  });

  it.each<Reason>(["busy", "unavailable", "unknown"])("%s retries with the button", async (reason) => {
    const onRetry = vi.fn();
    render(<ErrorState reason={reason} onRetry={onRetry} />);

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it.each<Reason>(["budget", "live_mode_off", "invalid_input", "not_found", "conflict"])(
    "%s offers no retry: it would fail the same way",
    (reason) => {
      render(<ErrorState reason={reason} onRetry={vi.fn()} retryHref="/venue/ven_1" />);

      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
      expect(screen.queryByRole("link", { name: "Try again" })).toBeNull();
    },
  );

  it("retries a server page with a full reload of its own path", () => {
    render(<ErrorState reason="unavailable" retryHref="/venue/ven_1/shortlist" />);

    const again = screen.getByRole("link", { name: "Try again" });
    expect(again.getAttribute("href")).toBe("/venue/ven_1/shortlist");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("lists the matches still to confirm on a conflict", () => {
    render(
      <ErrorState reason="conflict" details={{ pending: ["venue:Synthetic Room", "Synthetic Act"] }} />,
    );

    const list = screen.getByRole("list", { name: "Still to confirm" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "venue:Synthetic Room",
      "Synthetic Act",
    ]);
  });

  it("sends a refusal of live analysis to the seed the agent named", () => {
    render(<ErrorState reason="budget" details={{ fallbackSeed: "brooklyn-indie" }} />);

    expect(screen.getByRole("link", { name: "See a sample shortlist" }).getAttribute("href")).toBe(
      "/v/brooklyn-indie",
    );
  });

  it("names the refused field and leads back to the venue", () => {
    render(<ErrorState reason="invalid_input" details={{ field: "past_shows.0.act" }} />);

    expect(screen.getByRole("alert").textContent).toContain("past_shows.0.act");
    expect(screen.getByRole("link", { name: "Edit the venue" }).getAttribute("href")).toBe(
      "/venue/new",
    );
  });

  it("leads an outage back to the venue's crowd profile, as a panel heading", () => {
    render(<ErrorState reason="unavailable" details={{ venueId: "ven_1" }} heading="h2" compact />);

    expect(screen.getByRole("heading", { level: 2, name: "Can't reach Outdraw" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to crowd profile" }).getAttribute("href")).toBe(
      "/venue/ven_1",
    );
  });
});
