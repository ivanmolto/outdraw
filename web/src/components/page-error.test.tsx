// @vitest-environment jsdom
// The page boundaries (app/**/error.tsx): whatever threw, the booker sees the unknown
// state, a retry and at most the digest. Never the message, the stack or a URL.
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import RootError from "@/app/error";
import GlobalError from "@/app/global-error";
import VenueError from "@/app/venue/[id]/error";
import ShortlistError from "@/app/venue/[id]/shortlist/error";
import NewVenueError from "@/app/venue/new/error";
import DemoVenueError from "@/app/v/[slug]/error";
import { PageError, digestOf } from "./page-error";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "ven_fixture" }) }));

afterEach(cleanup);

/** What a development server would forward: the agent's URL and a stack. */
function leaky(): Error {
  const err = new Error("agent GET http://127.0.0.1:8787/venues/ven_1 failed: ECONNREFUSED");
  err.stack = `${err.message}\n    at transport (src/server/agent.ts:150:11)\n    at call (src/server/agent.ts:191:10)`;
  return err;
}

const LEAK = /8787|127\.0\.0\.1|ECONNREFUSED|agent\.ts|at transport|http:/;

describe("PageError", () => {
  it("shows the unknown state with a retry, and nothing from the error", async () => {
    const retry = vi.fn();
    const { container } = render(<PageError error={leaky()} retry={retry} />);

    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("data-reason")).toBe("unknown");
    expect(alert.textContent).toContain("Something went wrong on our side");
    expect(container.innerHTML).not.toMatch(LEAK);
    expect(screen.queryByText(/Error reference/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows the digest, the one thing that matches the server log", () => {
    const err = Object.assign(leaky(), { digest: "1234567890" });
    const { container } = render(<PageError error={err} retry={() => {}} />);

    expect(screen.getByText(/Error reference/).textContent).toContain("1234567890");
    expect(container.innerHTML).not.toMatch(LEAK);
  });

  it("renders for a thrown value that is no Error", () => {
    render(<PageError error="http://127.0.0.1:8787 is down" retry={() => {}} />);

    expect(screen.getByRole("alert").textContent).not.toMatch(LEAK);
  });

  it("leads back to the venue when the page has one", () => {
    render(<PageError error={leaky()} retry={() => {}} venueId="ven_1" />);

    expect(screen.getByRole("link", { name: "Back to crowd profile" }).getAttribute("href")).toBe(
      "/venue/ven_1",
    );
  });
});

describe("digestOf", () => {
  it("accepts Next's hash and refuses anything that could carry text", () => {
    expect(digestOf({ digest: "abc_-123" })).toBe("abc_-123");
    expect(digestOf({ digest: "NEXT_HTTP_ERROR_FALLBACK;404" })).toBeNull();
    expect(digestOf({ digest: "x".repeat(65) })).toBeNull();
    expect(digestOf({ digest: 12 })).toBeNull();
    expect(digestOf(new Error("plain"))).toBeNull();
    expect(digestOf(null)).toBeNull();
  });
});

describe("every page's boundary", () => {
  it.each<[string, (p: { error: unknown; retry: () => void }) => React.ReactNode, string]>([
    ["/", RootError, "Back to the start"],
    ["/venue/new", NewVenueError, "Back to the start"],
    ["/venue/[id]", VenueError, "Back to crowd profile"],
    ["/venue/[id]/shortlist", ShortlistError, "Back to crowd profile"],
    ["/v/[slug]", DemoVenueError, "Back to the start"],
  ])("%s shows the unknown state, a retry and a way out", (_route, Boundary, link) => {
    const retry = vi.fn();
    const { container } = render(<Boundary error={leaky()} retry={retry} />);

    expect(screen.getByRole("alert").getAttribute("data-reason")).toBe("unknown");
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("link", { name: link })).toBeTruthy();
    expect(container.innerHTML).not.toMatch(LEAK);
  });

  it("the root layout's boundary renders its own document", () => {
    // jsdom warns about <html> inside a div; the markup is what matters here.
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(<GlobalError error={leaky()} retry={() => {}} />);

    expect(screen.getByRole("alert").getAttribute("data-reason")).toBe("unknown");
    expect(container.innerHTML).not.toMatch(LEAK);
    vi.restoreAllMocks();
  });
});
