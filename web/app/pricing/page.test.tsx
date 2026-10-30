// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PRO_FEATURES, RUNS_PER_DAY } from "@/src/lib/entitlements";
import { HackathonNote, Pricing, ProAction } from "./page";

afterEach(cleanup);

describe("the pricing page", () => {
  it("lists Free and Venue Pro at $39, with every Pro feature and the run allowances", () => {
    render(<Pricing note={null} action={null} />);

    const plans = screen.getByRole("list", { name: "Plans" });
    const free = within(plans).getByRole("heading", { name: "Free" }).closest("li")!;
    const pro = within(plans).getByRole("heading", { name: /Venue Pro/ }).closest("li")!;
    expect(free).not.toBe(pro);
    expect(within(free).getByText("$0")).toBeTruthy();
    expect(within(free).getByText(`${RUNS_PER_DAY.free} shortlist runs a day`)).toBeTruthy();

    expect(within(pro).getByText("$39")).toBeTruthy();
    for (const label of Object.values(PRO_FEATURES)) {
      expect(within(pro).getByText(label)).toBeTruthy();
    }
    expect(within(pro).getByText(`${RUNS_PER_DAY.pro} shortlist runs a day`)).toBeTruthy();
  });

  it("puts paying for Pro on the roadmap, in plain words, with nothing to pay with today", () => {
    render(<Pricing note={null} action={null} />);
    expect(screen.getByRole("heading", { name: "What's next" })).toBeTruthy();
    expect(
      screen.getByText(/Paying for Pro is on the roadmap, and so is the artist side\. Nothing is charged today\./),
    ).toBeTruthy();
    expect(screen.getByText(/Upgrade to Venue Pro with a card, through Stripe\./)).toBeTruthy();
    // Artist routing is not built: it is a roadmap step, never a Pro feature on the card.
    expect(screen.getByRole("heading", { name: "Artist routing" })).toBeTruthy();
    const pro = within(screen.getByRole("list", { name: "Plans" }))
      .getByRole("heading", { name: /Venue Pro/ })
      .closest("li")!;
    expect(within(pro).queryByText("Artist routing")).toBeNull();
    // Build notes stay in docs/tasks.md, not on the page.
    expect(screen.queryByText(/test mode|webhook|live keys/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /Upgrade|Checkout|Subscribe/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Upgrade|Checkout|Subscribe/ })).toBeNull();
  });

  it("says Pro is free for everyone during the hackathon, under the title and on the card", () => {
    render(<HackathonNote hackathon />);
    expect(screen.getByText("Pro is free for everyone during the Qloo hackathon.")).toBeTruthy();
    expect(screen.getByText(/You are on it now: nothing to pay, nothing to set up\./)).toBeTruthy();
    cleanup();

    render(<ProAction hackathon />);
    expect(screen.getByText(/You are on Pro now, free, for the hackathon/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open a demo venue" }).getAttribute("href")).toBe("/#demos");
  });

  it("says checkout is not open yet otherwise, and offers the free plan", () => {
    render(<HackathonNote hackathon={false} />);
    expect(screen.queryByText(/hackathon/)).toBeNull();
    cleanup();

    render(<ProAction hackathon={false} />);
    expect(screen.getByText(/Checkout isn't open yet/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start free" }).getAttribute("href")).toBe("/venue/new");
  });
});
