"use client";

// Filters by genre tag and capacity band that narrow the shortlist in the browser: the order
// and the rank numbers stay the score's, and nothing is fetched. Genres are Qloo's tags for
// each act (read from its past-fit call); the band is Outdraw's reading of its Qloo
// popularity as the rooms it suits. `FilterGate` wraps each act in the list, so the list
// itself needs no filter logic.
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { SIZE_BANDS, type SizeBand } from "@/src/lib/run-result";

export interface BrowsableAct {
  entityId: string;
  name: string;
  rank: number;
  genres: string[];
  band: SizeBand | null;
}

/** The acts the filters let through, by entity ID; null outside a browser (all shown). */
const VisibleContext = createContext<Set<string> | null>(null);

/** Shows its act only while the filters let it through. */
export function FilterGate({ entityId, children }: { entityId: string; children: ReactNode }) {
  const visible = useContext(VisibleContext);
  return visible && !visible.has(entityId) ? null : children;
}

/** Genre tags across the acts, most common first, then by name. */
function genreOptions(acts: BrowsableAct[]): string[] {
  const counts = new Map<string, number>();
  for (const act of acts) for (const g of act.genres) counts.set(g, (counts.get(g) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([g]) => g);
}

/** The genre pill for acts Qloo gave no genre tags (outside the signalled top of the run, or
 * a past-fit call that came back less than ok), so a genre filter never hides them silently. */
export const NO_GENRE = "No genre tags";

/** An act passes when it has any chosen genre (or none are chosen; `NO_GENRE` chooses the
 * acts without any) and sits in the chosen band (or any band is allowed). */
export function filterActs(
  acts: BrowsableAct[],
  genres: ReadonlySet<string>,
  band: SizeBand | null,
): BrowsableAct[] {
  const byGenre = (a: BrowsableAct) =>
    genres.size === 0 ||
    a.genres.some((g) => genres.has(g)) ||
    (a.genres.length === 0 && genres.has(NO_GENRE));
  return acts.filter((a) => byGenre(a) && (!band || a.band === band));
}

const PILL = "rounded-full border px-2.5 py-1 text-xs font-medium";
const pill = (on: boolean) =>
  `${PILL} ${
    on ? "border-stage bg-stage text-brand-amber" : "border-ink/30 text-ink hover:bg-surface-2"
  }`;

/** The filter panel above the ranked list (`children`), whose acts it gates. */
export function ShortlistFilters({
  acts,
  children,
}: {
  acts: BrowsableAct[];
  children: ReactNode;
}) {
  const [genres, setGenres] = useState<ReadonlySet<string>>(new Set());
  const [band, setBand] = useState<SizeBand | null>(null);

  const genreList = useMemo(() => genreOptions(acts), [acts]);
  const untagged = acts.filter((a) => a.genres.length === 0).length;
  // A run's candidates are kept to the requested size tier before ranking, so most lists
  // sit in one band; the filter is offered only when there is a choice to make.
  const present = SIZE_BANDS.filter((b) => acts.some((a) => a.band === b.band));
  const bands = present.length > 1 ? present : [];
  const shown = useMemo(() => filterActs(acts, genres, band), [acts, genres, band]);
  const visible = useMemo(() => new Set(shown.map((a) => a.entityId)), [shown]);
  const filtered = genres.size > 0 || band !== null;

  const toggleGenre = (g: string) =>
    setGenres((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  const clear = () => {
    setGenres(new Set());
    setBand(null);
  };

  return (
    <>
      {(genreList.length > 0 || bands.length > 0) && (
        <section
          aria-label="Filters"
          className="mb-4 space-y-3 rounded-card border border-line bg-surface p-4"
        >
          {genreList.length > 0 && (
            <fieldset>
              <legend className="text-xs font-semibold text-ink/70">
                Genre <span className="font-normal">(Qloo tags)</span>
              </legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {[...genreList, ...(untagged > 0 ? [NO_GENRE] : [])].map((g) => (
                  <button
                    key={g}
                    type="button"
                    aria-pressed={genres.has(g)}
                    onClick={() => toggleGenre(g)}
                    className={pill(genres.has(g))}
                  >
                    {g === NO_GENRE ? `${g} (${untagged})` : g}
                  </button>
                ))}
              </div>
              {untagged > 0 && (
                <p className="mt-2 text-xs text-ink/60">
                  Qloo tagged {acts.length - untagged} of {acts.length} acts; the rest show
                  only under &ldquo;{NO_GENRE}&rdquo;.
                </p>
              )}
            </fieldset>
          )}
          {bands.length > 0 && (
            <fieldset>
              <legend className="text-xs font-semibold text-ink/70">
                Capacity band{" "}
                <span className="font-normal">(Outdraw&apos;s reading of Qloo popularity)</span>
              </legend>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  aria-pressed={band === null}
                  onClick={() => setBand(null)}
                  className={pill(band === null)}
                >
                  Any size
                </button>
                {bands.map((b) => (
                  <button
                    key={b.band}
                    type="button"
                    aria-pressed={band === b.band}
                    onClick={() => setBand(band === b.band ? null : b.band)}
                    className={pill(band === b.band)}
                  >
                    {b.label}
                  </button>
                ))}
              </div>
            </fieldset>
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs text-ink/70">
            <p aria-live="polite" data-testid="filter-count">
              {filtered
                ? `Showing ${shown.length} of ${acts.length} acts, in the score's order.`
                : `All ${acts.length} acts.`}
            </p>
            {filtered && (
              <button type="button" onClick={clear} className="font-medium text-ink underline">
                Clear filters
              </button>
            )}
          </div>
        </section>
      )}
      {filtered && shown.length === 0 && (
        <div role="status" className="mb-4 rounded-card border border-line bg-surface p-6">
          <p className="font-semibold">No acts match these filters</p>
          <p className="mt-2 text-sm text-ink/70">
            Clear a genre or widen the capacity band to see more of the shortlist.
          </p>
        </div>
      )}
      <VisibleContext.Provider value={filtered ? visible : null}>
        {children}
      </VisibleContext.Provider>
    </>
  );
}
