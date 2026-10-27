"use client";

// A date field in Outdraw's own colours: a text input that takes a typed date in a few
// common forms and normalises it to YYYY-MM-DD, with a month grid in a popover beside it.
// The grid follows the ARIA date-picker pattern: a dialog holding a grid, arrow keys move
// by day and week, Home/End to the week's ends, PageUp/PageDown by month (with Shift, by
// year), Enter or Space picks, Escape closes. On touch devices (a coarse pointer) the native
// <input type="date"> is kept: the phone's own picker is better than any web grid. Every
// browser draws its own native control and none of them can be themed, which is why this
// exists. The month grid is meant to serve the shortlist's holds calendar (T44) too.
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

// --- date arithmetic, in UTC so a calendar day never shifts with the zone ---------------

const pad = (n: number) => String(n).padStart(2, "0");

/** `Date.UTC` parts → "YYYY-MM-DD". */
export const toIso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;

/** "YYYY-MM-DD" → [year, month 1-12, day], or null. Rejects impossible dates (Feb 30). */
export function partsOf(iso: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) {
    return null;
  }
  return [y, mo, d];
}

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];

const monthIndex = (name: string) => {
  const key = name.toLowerCase().slice(0, 3);
  return MONTHS.findIndex((m) => m.startsWith(key));
};

/** A typed date as YYYY-MM-DD, or null. Accepts "2026-10-27", "27/10/2026", "27.10.2026",
 * "27 Oct 2026", "27 October 2026" and "Oct 27, 2026". Day-first for the numeric forms: the
 * placeholder says so, and the grid is there for anyone unsure. */
export function parseDate(text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  if (partsOf(t)) return t;
  let m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(t);
  if (m) {
    const iso = toIso(Number(m[3]), Number(m[2]), Number(m[1]));
    return partsOf(iso) && iso;
  }
  m = /^(\d{1,2})\s+([A-Za-z]{3,})\.?,?\s+(\d{4})$/.exec(t);
  if (m) {
    const mo = monthIndex(m[2]);
    if (mo === -1) return null;
    const iso = toIso(Number(m[3]), mo + 1, Number(m[1]));
    return partsOf(iso) && iso;
  }
  m = /^([A-Za-z]{3,})\.?\s+(\d{1,2}),?\s+(\d{4})$/.exec(t);
  if (m) {
    const mo = monthIndex(m[1]);
    if (mo === -1) return null;
    const iso = toIso(Number(m[3]), mo + 1, Number(m[2]));
    return partsOf(iso) && iso;
  }
  return null;
}

/** `iso` moved by `days` days. */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = partsOf(iso) ?? todayParts();
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return toIso(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

/** `iso` moved by `months` months, clamped to the target month's last day. */
export function addMonths(iso: string, months: number): string {
  const [y, m, d] = partsOf(iso) ?? todayParts();
  const first = new Date(Date.UTC(y, m - 1 + months, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  return toIso(first.getUTCFullYear(), first.getUTCMonth() + 1, Math.min(d, last));
}

function todayParts(): [number, number, number] {
  const now = new Date();
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()];
}

export const todayIso = () => toIso(...todayParts());

export interface GridDay {
  iso: string;
  day: number;
  inMonth: boolean;
}

/** The weeks shown for `iso`'s month, Monday first, padded with the neighbours' days. */
export function monthGrid(iso: string): GridDay[][] {
  const [y, m] = partsOf(iso) ?? todayParts();
  const first = new Date(Date.UTC(y, m - 1, 1));
  // Monday = 0 … Sunday = 6.
  const lead = (first.getUTCDay() + 6) % 7;
  const start = new Date(Date.UTC(y, m - 1, 1 - lead));
  const weeks: GridDay[][] = [];
  for (let w = 0; w < 6; w++) {
    const week: GridDay[] = [];
    for (let i = 0; i < 7; i++) {
      const date = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate() + w * 7 + i));
      week.push({
        iso: toIso(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate()),
        day: date.getUTCDate(),
        inMonth: date.getUTCMonth() === m - 1,
      });
    }
    weeks.push(week);
  }
  // Four to six rows: drop trailing weeks that belong wholly to the next month.
  while (weeks.length > 4 && weeks[weeks.length - 1].every((d) => !d.inMonth)) weeks.pop();
  return weeks;
}

const MONTH_LABEL = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const DAY_LABEL = new Intl.DateTimeFormat("en-GB", {
  weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
});
const label = (fmt: Intl.DateTimeFormat, iso: string) => {
  const [y, m, d] = partsOf(iso) ?? todayParts();
  return fmt.format(new Date(Date.UTC(y, m - 1, d)));
};

/** "October 2026" for any day of that month. */
export const monthLabel = (iso: string) => label(MONTH_LABEL, iso);

export const WEEKDAYS = [
  ["Mo", "Monday"], ["Tu", "Tuesday"], ["We", "Wednesday"], ["Th", "Thursday"],
  ["Fr", "Friday"], ["Sa", "Saturday"], ["Su", "Sunday"],
] as const;

// --- touch detection ------------------------------------------------------------------

const COARSE = "(pointer: coarse)";
const query = () => (typeof window.matchMedia === "function" ? window.matchMedia(COARSE) : null);
function subscribe(onChange: () => void) {
  const mq = query();
  mq?.addEventListener("change", onChange);
  return () => mq?.removeEventListener("change", onChange);
}
/** A coarse pointer (a phone or tablet): the native picker is the better one there. */
function useTouch(): boolean {
  return useSyncExternalStore(subscribe, () => query()?.matches ?? false, () => false);
}

// --- the grid ----------------------------------------------------------------------------

export function MonthGrid({
  value,
  onPick,
  onClose,
  labelledBy,
}: {
  /** The selected day (YYYY-MM-DD), or "" for none. */
  value: string;
  onPick: (iso: string) => void;
  onClose: () => void;
  labelledBy?: string;
}) {
  const today = todayIso();
  /** The day with the roving tab stop: the selection, else today. */
  const [focus, setFocus] = useState(partsOf(value) ? value : today);
  const grid = useRef<HTMLTableElement>(null);
  const headingId = useId();

  // Focus follows the roving day, so arrow keys read as moving a cursor.
  useEffect(() => {
    grid.current?.querySelector<HTMLButtonElement>(`[data-iso="${focus}"]`)?.focus();
  }, [focus]);

  const onKey = (e: React.KeyboardEvent, iso: string) => {
    const moves: Record<string, () => string> = {
      ArrowLeft: () => addDays(iso, -1),
      ArrowRight: () => addDays(iso, 1),
      ArrowUp: () => addDays(iso, -7),
      ArrowDown: () => addDays(iso, 7),
      Home: () => addDays(iso, -((dayOfWeek(iso) + 6) % 7)),
      End: () => addDays(iso, 6 - ((dayOfWeek(iso) + 6) % 7)),
      PageUp: () => addMonths(iso, e.shiftKey ? -12 : -1),
      PageDown: () => addMonths(iso, e.shiftKey ? 12 : 1),
    };
    const move = moves[e.key];
    if (move) {
      e.preventDefault();
      setFocus(move());
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onPick(iso);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  const weeks = monthGrid(focus);
  return (
    <div role="dialog" aria-modal="false" aria-labelledby={labelledBy ?? headingId}>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setFocus(addMonths(focus, -1))}
          aria-label="Previous month"
          className="h-8 w-8 rounded-md text-ink hover:bg-surface-2"
        >
          <span aria-hidden="true">‹</span>
        </button>
        <h3 id={headingId} aria-live="polite" className="display text-base">
          {label(MONTH_LABEL, focus)}
        </h3>
        <button
          type="button"
          onClick={() => setFocus(addMonths(focus, 1))}
          aria-label="Next month"
          className="h-8 w-8 rounded-md text-ink hover:bg-surface-2"
        >
          <span aria-hidden="true">›</span>
        </button>
      </div>
      <table ref={grid} role="grid" aria-labelledby={headingId} className="mt-2 w-full border-collapse">
        <thead>
          <tr>
            {WEEKDAYS.map(([short, long]) => (
              <th key={short} scope="col" className="h-8 text-xs font-semibold text-ink/60">
                <abbr title={long} className="no-underline">
                  {short}
                </abbr>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {weeks.map((week) => (
            <tr key={week[0].iso}>
              {week.map((d) => {
                const selected = d.iso === value;
                const isToday = d.iso === today;
                return (
                  <td key={d.iso} role="gridcell" aria-selected={selected} className="p-0 text-center">
                    <button
                      type="button"
                      data-iso={d.iso}
                      tabIndex={d.iso === focus ? 0 : -1}
                      aria-label={label(DAY_LABEL, d.iso)}
                      aria-current={isToday ? "date" : undefined}
                      onClick={() => onPick(d.iso)}
                      onKeyDown={(e) => onKey(e, d.iso)}
                      className={`h-9 w-9 rounded-md text-sm ${
                        selected
                          ? "bg-spot font-semibold text-paper"
                          : d.inMonth
                            ? "text-ink hover:bg-surface-2"
                            : "text-ink/60 hover:bg-surface-2"
                      } ${isToday && !selected ? "ring-1 ring-ink ring-inset" : ""}`}
                    >
                      {d.day}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex items-center justify-between text-sm">
        <button type="button" onClick={() => onPick("")} className="font-medium text-ink/70 underline hover:text-ink">
          Clear
        </button>
        <button type="button" onClick={() => onPick(today)} className="font-medium text-ink underline">
          Today
        </button>
      </div>
    </div>
  );
}

const dayOfWeek = (iso: string) => {
  const [y, m, d] = partsOf(iso) ?? todayParts();
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
};

// --- the field ---------------------------------------------------------------------------

interface Props {
  id: string;
  name: string;
  /** The draft's text: YYYY-MM-DD once picked or normalised, else whatever was typed. */
  value: string;
  onChange: (value: string) => void;
  /** Called with the value once it is settled: the field left (normalised when it reads as
   * a date), a day picked from the grid, or the native picker changed. */
  onCommit?: (value: string) => void;
  /** The input's classes (the form's shared input style). */
  className: string;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
}

export function DateField({ id, name, value, onChange, onCommit, className, ...aria }: Props) {
  const touch = useTouch();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialogLabel = useId();

  // A click outside closes the grid.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (touch) {
    return (
      <input
        id={id}
        name={name}
        type="date"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          onCommit?.(e.target.value);
        }}
        className={className}
        {...aria}
      />
    );
  }

  const close = (focusTrigger: boolean) => {
    setOpen(false);
    if (focusTrigger) trigger.current?.focus();
  };

  return (
    <div ref={root} className="relative">
      <input
        ref={input}
        id={id}
        name={name}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        value={value}
        placeholder="YYYY-MM-DD"
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => {
          const iso = parseDate(value);
          if (iso && iso !== value) onChange(iso);
          onCommit?.(iso ?? value);
        }}
        className={`${className} pr-11`}
        {...aria}
      />
      <span id={dialogLabel} className="sr-only">
        Choose a date
      </span>
      <button
        ref={trigger}
        type="button"
        aria-label="Choose a date"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="absolute top-1/2 right-1.5 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-ink/70 hover:bg-surface-2 hover:text-ink"
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="3" width="12" height="11" rx="1.5" />
          <path d="M2 6.5h12M5 1.5v3M11 1.5v3" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-72 rounded-card border border-line bg-surface p-4 shadow-lg">
          <MonthGrid
            value={parseDate(value) ?? ""}
            labelledBy={dialogLabel}
            onPick={(iso) => {
              onChange(iso);
              onCommit?.(iso);
              close(false);
              input.current?.focus();
            }}
            onClose={() => close(true)}
          />
        </div>
      )}
    </div>
  );
}
