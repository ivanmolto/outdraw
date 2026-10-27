"use client";

// The venue's pencil holds on a month grid, Monday first, a month at a time: each
// pencilled act sits on the night it holds, and holds with no night yet are listed under
// the grid. Holds span every run of the venue (one read of /api/venues/{id}/holds, our own
// rows, nothing spent), so refining the shortlist never hides what was pencilled before;
// the run on screen reads from the page's holds (holds.tsx), so a hold made in the
// Soundcheck panel shows at once.
import { useEffect, useState } from "react";

import { WEEKDAYS, addMonths, monthGrid, monthLabel, todayIso } from "@/src/components/date-field";
import { api } from "@/src/lib/api-client";
import { usePrecomputed } from "@/src/lib/precomputed";
import type { VenueHold } from "@/types/agent";
import { useHolds } from "./holds";
import type { BrowsableAct } from "./shortlist-filters";

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** "14 Nov 2026" for YYYY-MM-DD. */
export function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return DAY.format(new Date(Date.UTC(y, m - 1, d)));
}

/** A hold as the calendar places it. */
interface Hold {
  entityId: string;
  name: string;
  date: string | null;
  /** The act's rank on the run on screen, for the order within a night; none from other runs. */
  rank: number;
}

type Other =
  | { name: "loading" }
  | { name: "done"; holds: VenueHold[] }
  /** The read failed: the run on screen is still shown, with a note. */
  | { name: "failed" };

/** Holds read by venue: our own rows, read once per page. */
const loaded = new Map<string, VenueHold[]>();

/** Clears the module cache between tests. */
export function resetHoldsCache() {
  loaded.clear();
}

function useOtherRuns(venueId: string, runId: string): Other {
  const [state, setState] = useState<Other>(() => {
    const hit = loaded.get(venueId);
    return hit ? { name: "done", holds: hit } : { name: "loading" };
  });
  useEffect(() => {
    if (loaded.has(venueId)) return;
    let active = true;
    api.getHolds(venueId).then(
      (out) => {
        loaded.set(venueId, out.holds);
        if (active) setState({ name: "done", holds: out.holds });
      },
      () => {
        if (active) setState({ name: "failed" });
      },
    );
    return () => {
      active = false;
    };
  }, [venueId, runId]);
  return state;
}

/** The run on screen's holds (from the page, current) and every other run's (as read),
 * one entry per act and night. */
export function mergeHolds(
  current: Record<string, string | null>,
  acts: BrowsableAct[],
  others: VenueHold[],
  runId: string,
): Hold[] {
  const byId = new Map(acts.map((a) => [a.entityId, a]));
  const seen = new Set<string>();
  const out: Hold[] = [];
  const add = (hold: Hold) => {
    const key = `${hold.entityId} ${hold.date ?? ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(hold);
  };
  for (const [entityId, date] of Object.entries(current)) {
    const act = byId.get(entityId);
    if (act) add({ entityId, name: act.name, date, rank: act.rank });
  }
  for (const h of others) {
    if (h.run_id === runId) continue;
    add({ entityId: h.entity_id, name: h.name, date: h.date ?? null, rank: Infinity });
  }
  return out.sort(
    (a, b) =>
      (a.date ?? "").localeCompare(b.date ?? "") || a.rank - b.rank || a.name.localeCompare(b.name),
  );
}

const HOLD = "truncate rounded-sm bg-stage px-1 text-[11px] leading-tight text-brand-amber";

export function HoldsCalendar({
  venueId,
  runId,
  acts,
}: {
  venueId: string;
  runId: string;
  acts: BrowsableAct[];
}) {
  const current = useHolds()?.holds ?? {};
  const other = useOtherRuns(venueId, runId);
  const seed = usePrecomputed(runId) !== null;
  const held = mergeHolds(current, acts, other.name === "done" ? other.holds : [], runId);
  const dated = held.flatMap((h) => (h.date ? [{ ...h, date: h.date }] : []));
  const undated = held.filter((h) => h.date === null);

  const today = todayIso();
  // Opens on the first month from this one with a hold, else the last held, else this one.
  const [month, setMonth] = useState(
    () =>
      dated.find((h) => h.date.slice(0, 7) >= today.slice(0, 7))?.date ??
      dated.at(-1)?.date ??
      today,
  );
  const weeks = monthGrid(month);
  const onDay = new Map<string, Hold[]>();
  for (const h of dated) onDay.set(h.date, [...(onDay.get(h.date) ?? []), h]);
  const inMonth = dated.filter((h) => h.date.slice(0, 7) === month.slice(0, 7));

  return (
    <section
      aria-label="Pencil holds calendar"
      aria-busy={other.name === "loading"}
      className="rounded-card border border-line bg-surface p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setMonth(addMonths(month, -1))}
          aria-label="Previous month"
          className="h-8 w-8 rounded-md text-ink hover:bg-surface-2"
        >
          <span aria-hidden="true">‹</span>
        </button>
        <h2 aria-live="polite" className="display text-lg">
          {monthLabel(month)}
        </h2>
        <button
          type="button"
          onClick={() => setMonth(addMonths(month, 1))}
          aria-label="Next month"
          className="h-8 w-8 rounded-md text-ink hover:bg-surface-2"
        >
          <span aria-hidden="true">›</span>
        </button>
      </div>
      <table className="mt-3 w-full table-fixed border-collapse text-left">
        <caption className="sr-only">Pencilled acts in {monthLabel(month)}</caption>
        <thead>
          <tr>
            {WEEKDAYS.map(([short, long]) => (
              <th
                key={short}
                scope="col"
                className="pb-1 text-center text-xs font-semibold text-ink/60"
              >
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
                const names = onDay.get(d.iso) ?? [];
                return (
                  <td
                    key={d.iso}
                    data-date={d.iso}
                    className={`h-16 border border-line p-1 align-top sm:h-20 ${
                      d.inMonth ? "" : "bg-surface-2/50"
                    }`}
                  >
                    <span
                      className={`block text-xs ${d.inMonth ? "text-ink/70" : "text-ink/40"} ${
                        d.iso === today ? "font-semibold text-ink" : ""
                      }`}
                    >
                      {d.day}
                    </span>
                    {names.length > 0 && (
                      <ul className="mt-0.5 space-y-0.5">
                        {names.map((h) => (
                          <li
                            key={h.entityId}
                            data-testid="calendar-hold"
                            className={HOLD}
                            title={h.name}
                          >
                            {h.name}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {other.name === "failed" && (
        <p role="status" className="mt-3 text-xs text-ink/70">
          Couldn&apos;t read the holds from this venue&apos;s other runs; this run&apos;s are
          shown.
        </p>
      )}
      {held.length === 0 ? (
        <p className="mt-4 text-sm text-ink/70">
          {seed
            ? "Pencil holds are off on the demo. Refine the shortlist for a live copy."
            : "No pencil holds yet. Open an act's Soundcheck and pencil a hold to place it here."}
        </p>
      ) : (
        <div className="mt-4 space-y-3 text-sm">
          {inMonth.length > 0 && (
            <ul aria-label={`Holds in ${monthLabel(month)}`} className="space-y-1">
              {inMonth.map((h) => (
                <li key={`${h.entityId} ${h.date}`} className="flex gap-2">
                  <span className="w-28 shrink-0 font-mono text-xs text-ink/70">
                    {dayLabel(h.date)}
                  </span>
                  <span className="text-ink">{h.name}</span>
                </li>
              ))}
            </ul>
          )}
          {undated.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-ink/70">Pencilled, no date yet</p>
              <ul aria-label="Pencilled, no date yet" className="mt-1 flex flex-wrap gap-2">
                {undated.map((h) => (
                  <li
                    key={h.entityId}
                    className="rounded-full border border-ink/30 px-2 py-0.5 text-xs"
                  >
                    {h.name}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
