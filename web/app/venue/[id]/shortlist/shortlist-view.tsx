// The ranked shortlist: one row per act in the agent's order (the deterministic score, never
// the model's), with the total as a bar, the templated top reason and a chip per sub-score.
// Chips say where each number comes from (the agent's `source_labels`): Qloo data, Outdraw's
// own data, or the run's median of the measured acts where Qloo gave no signal. No hooks: rendered by the server page
// for a stored run and by the client run component for a fresh one.
import Link from "next/link";

import {
  SUB_SCORES,
  type ShortlistEntry,
  type ShortlistResult,
  type SubScore,
} from "@/src/lib/run-result";
import { SourceBadge } from "../profile-view";

const LABELS: Record<SubScore, string> = {
  crowd_affinity: "Crowd affinity",
  past_fit: "Past fit",
  local_heat: "Local heat",
  novelty: "Novelty",
  size_fit: "Size fit",
  momentum: "Momentum",
};

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
          ? "rounded-full bg-ink px-2 py-0.5 text-paper"
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

function Act({ entry, rank }: { entry: ShortlistEntry; rank: number }) {
  const reason = entry.top_reason ?? entry.reason;
  const neutral = new Set(entry.neutral ?? []);
  const labels = entry.source_labels ?? {};
  return (
    <li className="rounded-lg border border-ink/15 bg-white/60 p-4">
      <div className="flex items-baseline gap-3">
        <span className="shrink-0 font-mono text-sm text-ink/50" aria-hidden="true">
          {rank}
        </span>
        <h2 className="min-w-0 flex-1 font-semibold break-words text-ink">{entry.name}</h2>
        <span className="shrink-0 font-mono text-sm text-ink" data-testid="total">
          <span className="sr-only">Score </span>
          {entry.total.toFixed(2)}
        </span>
      </div>
      <div
        aria-hidden="true"
        data-testid="total-bar"
        className="mt-2 h-2 w-full rounded-full bg-ink/10"
      >
        <div className="h-full rounded-full bg-ink" style={{ width: pct(entry.total) }} />
      </div>
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
      className="mb-6 rounded-lg border border-ink/30 bg-white p-4 text-sm"
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

function Header({ venueName }: { venueName: string }) {
  return (
    <header className="mb-6">
      <p className="text-sm font-medium text-ink/60">Shortlist</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight break-words">{venueName}</h1>
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
  result,
}: {
  venueId: string;
  venueName: string;
  result: ShortlistResult;
}) {
  return (
    <>
      <Header venueName={venueName} />
      <QualityBanner result={result} />
      {result.shortlist.length === 0 ? (
        <div role="alert" className="rounded-lg border border-ink/15 bg-white/60 p-6">
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
              <span className="h-3 w-3 rounded-full bg-ink" aria-hidden="true" /> Qloo data
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
          <ol aria-label="Ranked acts" className="space-y-3">
            {result.shortlist.map((entry, i) => (
              <Act key={entry.entity_id} entry={entry} rank={i + 1} />
            ))}
          </ol>
        </>
      )}
      <Link
        href={`/venue/${encodeURIComponent(venueId)}`}
        className="mt-8 inline-block text-sm font-medium underline"
      >
        Back to crowd profile
      </Link>
    </>
  );
}

function Line({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-ink/10 ${className}`} />;
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
            className="space-y-3 rounded-lg border border-ink/15 bg-white/60 p-4"
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
