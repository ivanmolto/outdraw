"use client";

// Ways through a shortlist without changing it: the filters (shortlist-filters.tsx) and a
// List / Calendar switch whose calendar places the pencilled acts on the nights they hold
// (holds-calendar.tsx), and the export to a booking tool (export-menu.tsx). Holds the run's
// pencil holds for all three (holds.tsx).
import { useState, type ReactNode } from "react";

import type { ExportAct } from "@/src/lib/export";
import { ExportMenu } from "./export-menu";
import { HoldsProvider, type Holds } from "./holds";
import { HoldsCalendar } from "./holds-calendar";
import { ShortlistFilters, type BrowsableAct } from "./shortlist-filters";

export { FilterGate, type BrowsableAct } from "./shortlist-filters";

type View = "list" | "calendar";

export function ShortlistBrowser({
  venueId,
  venueName,
  runId,
  acts,
  exportActs,
  holds,
  children,
}: {
  venueId: string;
  venueName: string;
  runId: string;
  acts: BrowsableAct[];
  /** The acts as the export writes them, in shortlist order. */
  exportActs: ExportAct[];
  /** The holds stored with the run. */
  holds: Holds;
  /** The ranked list, its acts wrapped in FilterGate. */
  children: ReactNode;
}) {
  return (
    <HoldsProvider runId={runId} initial={holds}>
      <Browser
        venueId={venueId}
        venueName={venueName}
        runId={runId}
        acts={acts}
        exportActs={exportActs}
      >
        {children}
      </Browser>
    </HoldsProvider>
  );
}

function Browser({
  venueId,
  venueName,
  runId,
  acts,
  exportActs,
  children,
}: {
  venueId: string;
  venueName: string;
  runId: string;
  acts: BrowsableAct[];
  exportActs: ExportAct[];
  children: ReactNode;
}) {
  const [view, setView] = useState<View>("list");
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div
          role="group"
          aria-label="View"
          className="inline-flex rounded-full border border-line p-0.5"
        >
          {(["list", "calendar"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={`rounded-full px-3 py-1 text-sm font-medium ${
                view === v ? "bg-stage text-brand-amber" : "text-ink"
              }`}
            >
              {v === "list" ? "List" : "Calendar"}
            </button>
          ))}
        </div>
        <ExportMenu venueName={venueName} runId={runId} acts={exportActs} />
      </div>
      {view === "list" ? (
        <ShortlistFilters acts={acts}>{children}</ShortlistFilters>
      ) : (
        <HoldsCalendar venueId={venueId} runId={runId} acts={acts} />
      )}
    </>
  );
}
