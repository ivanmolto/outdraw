// The ranked shortlist: one row per act in the agent's order (the deterministic score, never
// the model's), with the total as a bar, the templated top reason and a chip per sub-score.
// Chips say where each number comes from (the agent's `source_labels`): Qloo data, Outdraw's
// own data, or the run's median of the measured acts where Qloo gave no signal. No hooks: rendered by the server page
// for a stored run and by the client run component for a fresh one. Each scored act opens
// its Soundcheck panel, and the list sits inside the With Qloo / LLM only toggle (both
// client components). After a refinement each act carries a badge for how it changed
// against the parent run, and the acts that left are listed under it. An act that has played
// the venue carries a "Played here" chip from the venue's own past shows; the list can be
// filtered by genre and capacity band, or seen as a calendar of pencil holds
// (shortlist-browser.tsx).
import Link from "next/link";
import type { ReactNode } from "react";

import {
  SUB_SCORES,
  SUB_SCORE_LABELS as LABELS,
  type ShortlistEntry,
  type ShortlistResult,
  type SubScore,
} from "@/src/lib/run-result";
import type { PlayedHere, PlayedHereMap } from "@/src/lib/played-here";
import type { ActChange, ShortlistDiff } from "@/src/lib/shortlist-diff";
import type { CrossDomain } from "@/src/lib/soundcheck";
import { SourceBadge } from "../profile-view";
import { CompareToggle } from "./compare-toggle";
import { PrecomputedLabel } from "./precomputed-label";
import { dayLabel } from "./holds-calendar";
import { FilterGate, ShortlistBrowser, type BrowsableAct } from "./shortlist-browser";
import { ChangeBadge, LeftList } from "./shortlist-diff";
import { SoundcheckButton } from "./soundcheck";

const pct = (value: number) => `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%`;

function Chip({
  name,
  value,
  neutral,
  qloo,
}: {
  name: SubScore;
  value: number;
  neutral: boolean;
  qloo: boolean;
}) {
  const label = LABELS[name];
  if (neutral) {
    return (
      <li
        data-testid="chip"
        data-source="neutral"
        title="No Qloo signal: scored the median of the acts Qloo measured in this run"
        className="rounded-full border border-dashed border-ink/40 px-2 py-0.5 text-ink/60"
      >
        {label} <span className="font-mono">{value.toFixed(2)}</span>
        <span className="sr-only"> (no Qloo signal, run median)</span>
      </li>
    );
  }
  return (
    <li
      data-testid="chip"
      data-source={qloo ? "qloo" : "outdraw"}
      title={qloo ? "Qloo data" : "Outdraw's own data"}
      className={
        qloo
          ? "rounded-full bg-stage px-2 py-0.5 text-house ring-1 ring-line"
          : "rounded-full border border-ink/30 px-2 py-0.5 text-ink"
      }
    >
      {label} <span className={qloo ? "font-mono text-brand-amber" : "font-mono"}>
        {value.toFixed(2)}
      </span>
      <span className="sr-only">{qloo ? " (Qloo data)" : " (Outdraw)"}</span>
    </li>
  );
}

/** The venue's own record of the act: its last night here and how much of the room it sold. */
export function PlayedChip({ played }: { played: PlayedHere }) {
  const sold = played.sellThrough == null ? null : Math.round(played.sellThrough * 100);
  return (
    <p
      data-testid="played-here"
      title="From your past shows (Outdraw data)"
      className="inline-flex flex-wrap items-center gap-x-1.5 rounded-full border border-ink/30 px-2 py-0.5 text-xs text-ink"
    >
      <span className="font-medium">Played here</span>
      <span aria-hidden="true">·</span>
      <time dateTime={played.date}>{dayLabel(played.date)}</time>
      {sold !== null && (
        <>
          <span aria-hidden="true">·</span>
          <span className="font-mono">{sold}% sold</span>
        </>
      )}
      <span className="sr-only"> (Outdraw data)</span>
    </p>
  );
}

/** Totals are shown to two decimals, except where a neighbour rounds to the same two: then
 * both show three, so the order never looks like a tie the score didn't break. */
export function precisionOf(totals: readonly number[]): number[] {
  const rounded = totals.map((t) => t.toFixed(2));
  return totals.map((_, i) =>
    rounded[i] === rounded[i - 1] || rounded[i] === rounded[i + 1] ? 3 : 2,
  );
}

function Act({
  entry,
  rank,
  runId,
  crossDomain,
  change,
  played,
  precision = 2,
}: {
  entry: ShortlistEntry;
  rank: number;
  runId: string;
  crossDomain?: CrossDomain;
  change?: ActChange;
  played?: PlayedHere;
  /** Decimals of the total (see precisionOf). */
  precision?: number;
}) {
  const reason = entry.top_reason ?? entry.reason;
  const neutral = new Set(entry.neutral ?? []);
  const labels = entry.source_labels ?? {};
  return (
    <li
      data-change={change?.kind}
      className={`rounded-card border bg-surface p-4 ${
        change?.kind === "entered" ? "border-good border-l-4" : "border-line"
      }`}
    >
      <div className="flex items-baseline gap-3">
        <span className="display w-6 shrink-0 text-xl text-ink/60" aria-hidden="true">
          {rank}
        </span>
        <h2 className="display min-w-0 flex-1 text-xl break-words text-ink">{entry.name}</h2>
        <ChangeBadge change={change} />
        <span
          className="shrink-0 font-mono text-sm text-ink"
          data-testid="total"
          title={
            precision > 2
              ? "Three decimals: a neighbouring act rounds to the same score"
              : undefined
          }
        >
          <span className="sr-only">Score </span>
          {entry.total.toFixed(precision)}
        </span>
      </div>
      <div
        aria-hidden="true"
        data-testid="total-bar"
        className="mt-2 h-2 w-full rounded-full bg-stage ring-1 ring-line"
      >
        <div className="h-full rounded-full bg-brand-amber" style={{ width: pct(entry.total) }} />
      </div>
      {(played || (entry.genres?.length ?? 0) > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
          {played && <PlayedChip played={played} />}
          {entry.genres && entry.genres.length > 0 && (
            <p className="text-xs text-ink/60" title="Qloo genre tags">
              <span className="sr-only">Genres (Qloo data): </span>
              {entry.genres.join(" · ")}
            </p>
          )}
        </div>
      )}
      {reason && <p className="mt-3 text-sm text-ink/80">{reason}</p>}
      {entry.sub_scores && (
        <ul aria-label={`Sub-scores for ${entry.name}`} className="mt-3 flex flex-wrap gap-2 text-xs">
          {SUB_SCORES.map((name) => {
            const value = entry.sub_scores?.[name];
            return value == null ? null : (
              <Chip
                key={name}
                name={name}
                value={value}
                neutral={neutral.has(name)}
                qloo={labels[name] === "qloo"}
              />
            );
          })}
        </ul>
      )}
      {entry.sub_scores && (
        <div className="mt-3">
          <SoundcheckButton
            runId={runId}
            entityId={entry.entity_id}
            name={entry.name}
            crossDomain={crossDomain}
          />
        </div>
      )}
    </li>
  );
}

function QualityBanner({ result }: { result: ShortlistResult }) {
  if (result.data_quality === "ok" && result.notes.length === 0) return null;
  const heading =
    result.data_quality === "degraded"
      ? "Parts of this shortlist rest on missing Qloo data."
      : result.data_quality === "partial"
        ? "Some Qloo signals behind this shortlist are partial."
        : "Notes on this shortlist.";
  return (
    <aside
      aria-label="Data quality"
      className="mb-6 rounded-card border border-ink/30 bg-surface p-4 text-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium text-ink">{heading}</p>
        <SourceBadge source="outdraw" />
      </div>
      {result.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-ink/70">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function Header({ venueName, runId }: { venueName: string; runId: string }) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-ink/60">Shortlist</p>
        <PrecomputedLabel runId={runId} />
      </div>
      <h1 className="mt-1 display text-3xl sm:text-4xl break-words">{venueName}</h1>
      <p className="mt-2 text-sm text-ink/70">
        Acts ranked by Outdraw&apos;s score: a fixed weighting of Qloo signals and your
        venue&apos;s own data. The model explains; it never orders.
      </p>
    </header>
  );
}

export function ShortlistView({
  venueId,
  venueName,
  runId,
  result,
  crossDomain,
  diff,
  played = {},
  profileHref = `/venue/${encodeURIComponent(venueId)}`,
  children,
}: {
  venueId: string;
  venueName: string;
  runId: string;
  result: ShortlistResult;
  /** The crowd profile's places, films and brands, for Soundcheck's cross-domain strip. */
  crossDomain?: CrossDomain;
  /** The venue's past shows of the shortlisted acts, by entity ID. */
  played?: PlayedHereMap;
  /** Changes against the parent run, for a refined run. */
  diff?: ShortlistDiff;
  /** Where "Back to crowd profile" goes: the venue page, or the profile above on a seed page. */
  profileHref?: string;
  /** Shown under the list: the refinement box. */
  children?: ReactNode;
}) {
  const precision = precisionOf(result.shortlist.map((e) => e.total));
  const acts: BrowsableAct[] = result.shortlist.map((e, i) => ({
    entityId: e.entity_id,
    name: e.name,
    rank: i + 1,
    genres: e.genres ?? [],
    band: e.size_band ?? null,
  }));
  const holds = Object.fromEntries(
    result.shortlist.flatMap((e) => (e.pencil_hold ? [[e.entity_id, e.pencil_hold.date ?? null]] : [])),
  );
  return (
    <>
      <Header venueName={venueName} runId={runId} />
      <QualityBanner result={result} />
      {result.shortlist.length === 0 ? (
        <div role="alert" className="rounded-card border border-line bg-surface p-6">
          <p className="font-semibold">No acts made the shortlist</p>
          <p className="mt-2 text-sm text-ink/70">
            Qloo found no candidates that fit this venue&apos;s constraints.
          </p>
        </div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink/70">
            <span className="flex items-center gap-1.5">
              <SourceBadge source="outdraw" /> Total score
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-full bg-stage ring-1 ring-line" aria-hidden="true" />{" "}
              Qloo data
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-full border border-ink/30" aria-hidden="true" />{" "}
              Outdraw data
            </span>
            <span className="flex items-center gap-1.5">
              <span
                className="h-3 w-3 rounded-full border border-dashed border-ink/40"
                aria-hidden="true"
              />{" "}
              No Qloo signal (run median)
            </span>
          </div>
          <ShortlistBrowser venueId={venueId} runId={runId} acts={acts} holds={holds}>
            <CompareToggle runId={runId} qlooNames={result.shortlist.map((e) => e.name)}>
              <ol aria-label="Ranked acts" className="space-y-3">
                {result.shortlist.map((entry, i) => (
                  <FilterGate key={entry.entity_id} entityId={entry.entity_id}>
                    <Act
                      entry={entry}
                      rank={i + 1}
                      runId={runId}
                      crossDomain={crossDomain}
                      change={diff?.changes.get(entry.entity_id)}
                      played={played[entry.entity_id]}
                      precision={precision[i]}
                    />
                  </FilterGate>
                ))}
              </ol>
            </CompareToggle>
          </ShortlistBrowser>
        </>
      )}
      <LeftList diff={diff} />
      {children}
      <Link href={profileHref} className="mt-8 inline-block text-sm font-medium underline">
        Back to crowd profile
      </Link>
    </>
  );
}

function Line({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-surface-2 ${className}`} />;
}

/** Same footprint as ShortlistView, shown while the agent runs or the page loads. */
export function ShortlistSkeleton({ label = "Loading shortlist" }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label}>
      <div className="mb-6 space-y-2">
        <Line className="h-4 w-20" />
        <Line className="h-7 w-2/3" />
        <Line className="h-4 w-full max-w-md" />
      </div>
      <ol className="space-y-3">
        {[0, 1, 2, 3, 4].map((i) => (
          <li
            key={i}
            data-testid="skeleton-act"
            className="space-y-3 rounded-card border border-line bg-surface p-4"
          >
            <div className="flex justify-between gap-3">
              <Line className="h-5 w-1/2" />
              <Line className="h-5 w-10" />
            </div>
            <Line className="h-2 w-full" />
            <Line className="h-3 w-5/6" />
            <div className="flex flex-wrap gap-2">
              {[0, 1, 2, 3, 4].map((j) => (
                <Line key={j} className="h-5 w-20 rounded-full" />
              ))}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
