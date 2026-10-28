"use client";

// A finished run with its refinements: the run on screen (with its diff against its
// parent), the refinement box under it and the run-history sidebar beside it. A refinement
// chains to the run on screen, so refining an earlier run drops the later ones from the
// chain shown. The latest run's id goes into the URL (`?run=`), like a fresh run's. On a seed
// page (/v/[slug]) a refinement forks the read-only seed into a live run, and the URL moves to
// that run's live shortlist page, where a reload or a shared link finds it. A refinement
// refused for want of live analysis (busy, budget, LIVE_MODE=off) swaps in the closest seed's
// sample until the booker goes back to their own shortlist.
import { useState } from "react";

import { ProNote, useShows } from "@/src/components/pro";
import { api, errorOf, sampleSeed } from "@/src/lib/api-client";
import type { PlayedHereMap } from "@/src/lib/played-here";
import { parseRun, type ParsedRun } from "@/src/lib/run-result";
import type { SeedSlug } from "@/src/lib/seeds";
import { diffShortlists, type ShortlistDiff } from "@/src/lib/shortlist-diff";
import type { CrossDomain } from "@/src/lib/soundcheck";
import { RefineBox, type RefineError } from "./refine-box";
import { RunHistory } from "./run-history";
import { RunOutcome } from "./run-outcome";
import { SampleFallback } from "./sample-fallback";

/** The diff of `run` against `parent`, when both are shortlists and `parent` is its own. */
function diffWith(run: ParsedRun, parent: ParsedRun | undefined): ShortlistDiff | undefined {
  if (run.kind !== "shortlist" || parent?.kind !== "shortlist") return undefined;
  if (run.parentRunId !== parent.runId) return undefined;
  return diffShortlists(parent.shortlist, run.shortlist);
}

interface Props {
  venueId: string;
  venueName: string;
  /** Oldest first; the last is the run to show. */
  chain: ParsedRun[];
  crossDomain?: CrossDomain;
  /** The venue's past shows of the shortlisted acts (src/lib/played-here.ts). */
  played?: PlayedHereMap;
  /** The path a refined run's `?run=` goes on; the current page when absent. */
  shortlistPath?: string;
  profileHref?: string;
  /** False hides the refinement box (a sample shown in place of a live run). */
  refinable?: boolean;
}

export function RefinableRun({
  venueId,
  venueName,
  chain: initial,
  crossDomain,
  played,
  shortlistPath = "",
  profileHref,
  refinable = true,
}: Props) {
  const [chain, setChain] = useState(initial);
  const [selected, setSelected] = useState(initial.length - 1);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<RefineError | null>(null);
  const [sample, setSample] = useState<SeedSlug | null>(null);
  const history = useShows("run_history");

  const run = chain[selected];
  const diff = diffWith(run, chain[selected - 1]);

  function refine(message: string) {
    if (pending) return;
    const base = selected;
    setPending(true);
    setError(null);
    api
      .refineRun(chain[base].runId, { message })
      .then(parseRun)
      .then(
        (next) => {
          const kept = chain.slice(0, base + 1);
          setChain([...kept, next]);
          setSelected(kept.length);
          window.history.replaceState(
            null,
            "",
            `${shortlistPath}?run=${encodeURIComponent(next.runId)}`,
          );
        },
        (err: unknown) => {
          const seed = sampleSeed(err);
          if (seed) setSample(seed);
          else setError(errorOf(err));
        },
      )
      .finally(() => setPending(false));
  }

  if (sample) {
    const back = (
      <button type="button" onClick={() => setSample(null)} className="font-medium underline">
        Back to your shortlist
      </button>
    );
    return (
      <SampleFallback
        slug={sample}
        action={back}
        fallback={
          <div role="alert" className="rounded-card border border-line bg-surface p-6 text-sm">
            <p className="text-ink/70">Live analysis is busy right now. Try again in a moment.</p>
            <div className="mt-3">{back}</div>
          </div>
        }
      />
    );
  }

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_15rem] lg:gap-8">
      <div aria-busy={pending}>
        <RunOutcome
          venueId={venueId}
          venueName={venueName}
          run={run}
          crossDomain={crossDomain}
          diff={diff}
          played={played}
          profileHref={profileHref}
        >
          {/* Keyed by run: a new run on screen starts with an empty box. */}
          {refinable && (
            <RefineBox key={run.runId} pending={pending} error={error} onRefine={refine} />
          )}
        </RunOutcome>
      </div>
      {chain.length > 1 && (
        <div className="mt-8 lg:mt-0">
          {!history ? (
            <ProNote feature="run_history" />
          ) : (
            <RunHistory
              chain={chain}
              selected={selected}
              onSelect={(i) => {
                if (pending) return;
                setSelected(i);
                setError(null);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
