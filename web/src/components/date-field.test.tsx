// @vitest-environment jsdom
// The date field: typed dates normalise to YYYY-MM-DD, the month grid picks and moves with
// the keyboard, and a coarse pointer gets the native input instead.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DateField, addDays, addMonths, monthGrid, parseDate } from "./date-field";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("parseDate", () => {
  it("accepts the common written forms, day first", () => {
    expect(parseDate("2026-10-27")).toBe("2026-10-27");
    expect(parseDate("27/10/2026")).toBe("2026-10-27");
    expect(parseDate("27.10.2026")).toBe("2026-10-27");
    expect(parseDate("7-3-2026")).toBe("2026-03-07");
    expect(parseDate("27 Oct 2026")).toBe("2026-10-27");
    expect(parseDate("27 October 2026")).toBe("2026-10-27");
    expect(parseDate("Oct 27, 2026")).toBe("2026-10-27");
    expect(parseDate("  2026-10-27 ")).toBe("2026-10-27");
  });

  it("rejects what is not a date", () => {
    expect(parseDate("")).toBeNull();
    expect(parseDate("soon")).toBeNull();
    expect(parseDate("2026-02-30")).toBeNull();
    expect(parseDate("31/04/2026")).toBeNull();
    expect(parseDate("27 Foo 2026")).toBeNull();
    expect(parseDate("10/27/2026")).toBeNull();
  });
});

describe("date arithmetic", () => {
  it("moves by days and months, clamping to the month's end", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-10-27", -12)).toBe("2025-10-27");
  });

  it("lays a month out Monday first, padded with its neighbours", () => {
    const weeks = monthGrid("2026-10-27");
    expect(weeks).toHaveLength(5);
    expect(weeks[0].map((d) => d.day)).toEqual([28, 29, 30, 1, 2, 3, 4]);
    expect(weeks[0].slice(0, 3).every((d) => !d.inMonth)).toBe(true);
    expect(weeks[4].map((d) => d.day)).toEqual([26, 27, 28, 29, 30, 31, 1]);
    // February 2027 starts on a Monday and fits in four rows.
    expect(monthGrid("2027-02-10")).toHaveLength(4);
  });
});

function setup(value = "") {
  const onChange = vi.fn();
  const view = render(
    <DateField id="d" name="date" value={value} onChange={onChange} className="input" />,
  );
  const rerender = (next: string) =>
    view.rerender(<DateField id="d" name="date" value={next} onChange={onChange} className="input" />);
  return { onChange, rerender };
}

describe("DateField", () => {
  it("is a text input that normalises a typed date on blur", async () => {
    const user = userEvent.setup();
    const { onChange, rerender } = setup();
    const input = screen.getByRole("textbox");
    expect(input.getAttribute("placeholder")).toBe("YYYY-MM-DD");

    await user.type(input, "27/10/2026");
    // Controlled: the parent feeds the typed text back.
    rerender("27/10/2026");
    fireEvent.blur(input);
    expect(onChange).toHaveBeenLastCalledWith("2026-10-27");
  });

  it("leaves text it cannot read for validation to flag", () => {
    const { onChange, rerender } = setup();
    rerender("soon");
    fireEvent.blur(screen.getByRole("textbox"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("opens a grid on the selected month and picks a day", async () => {
    const user = userEvent.setup();
    const { onChange } = setup("2026-10-27");
    await user.click(screen.getByRole("button", { name: "Choose a date" }));

    const dialog = screen.getByRole("dialog", { name: "Choose a date" });
    expect(within(dialog).getByRole("heading", { name: "October 2026" })).toBeTruthy();
    const selected = within(dialog).getByRole("button", { name: /^Tuesday,? 27 October 2026$/ });
    expect(selected.closest("[role=gridcell]")?.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(selected);

    await user.click(within(dialog).getByRole("button", { name: /^Friday,? 30 October 2026$/ }));
    expect(onChange).toHaveBeenLastCalledWith("2026-10-30");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
  });

  it("moves the focus with the keyboard and picks with Enter", async () => {
    const user = userEvent.setup();
    const { onChange } = setup("2026-10-27");
    await user.click(screen.getByRole("button", { name: "Choose a date" }));
    const dialog = screen.getByRole("dialog", { name: "Choose a date" });

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement?.getAttribute("data-iso")).toBe("2026-10-28");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement?.getAttribute("data-iso")).toBe("2026-11-04");
    expect(within(dialog).getByRole("heading", { name: "November 2026" })).toBeTruthy();
    await user.keyboard("{Home}");
    expect(document.activeElement?.getAttribute("data-iso")).toBe("2026-11-02");
    await user.keyboard("{PageUp}");
    expect(document.activeElement?.getAttribute("data-iso")).toBe("2026-10-02");
    await user.keyboard("{Shift>}{PageDown}{/Shift}");
    expect(document.activeElement?.getAttribute("data-iso")).toBe("2027-10-02");
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith("2027-10-02");
  });

  it("closes on Escape and hands focus back to the button", async () => {
    const user = userEvent.setup();
    setup("2026-10-27");
    const trigger = screen.getByRole("button", { name: "Choose a date" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("clears and jumps to today", async () => {
    const user = userEvent.setup();
    const { onChange } = setup("2026-10-27");
    await user.click(screen.getByRole("button", { name: "Choose a date" }));
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("keeps the native input on a coarse pointer", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: q === "(pointer: coarse)",
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    setup("2026-10-27");
    const input = document.querySelector("input");
    expect(input?.getAttribute("type")).toBe("date");
    expect(screen.queryByRole("button", { name: "Choose a date" })).toBeNull();
  });
});
