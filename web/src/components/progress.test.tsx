// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Elapsed, Spinner, formatElapsed } from "./progress";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("formatElapsed", () => {
  it("shows minutes and zero-padded seconds", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(9)).toBe("0:09");
    expect(formatElapsed(75)).toBe("1:15");
  });
});

describe("Elapsed", () => {
  it("counts up once a second", () => {
    render(<Elapsed />);
    const timer = screen.getByRole("timer", { name: "Time elapsed" });
    expect(timer.textContent).toBe("0:00 elapsed");
    act(() => vi.advanceTimersByTime(12_000));
    expect(timer.textContent).toBe("0:12 elapsed");
  });

  it("stops ticking once unmounted", () => {
    const { unmount } = render(<Elapsed />);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("Spinner", () => {
  it("is decorative and stands still under reduced motion", () => {
    render(<Spinner />);
    const spinner = screen.getByTestId("spinner");
    expect(spinner.getAttribute("aria-hidden")).toBe("true");
    expect(spinner.className).toContain("motion-reduce:animate-none");
  });
});
