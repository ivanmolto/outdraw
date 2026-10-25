"use client";

// A finished run with its refinements: the run on screen (with its diff against its
// parent), the refinement box under it and the run-history sidebar beside it. A refinement
// chains to the run on screen, so refining an earlier run drops the later ones from the
// chain shown. The latest run's id goes into the URL (`?run=`), like a fresh run's.
import { useState } from "react";

import { ApiError, api, type Reason } from "@/src/lib/api-client";
import { parseRun, type ParsedRun } from "@/src/lib/run-result";
import { diffShortlists, type ShortlistDiff } from "@/src/lib/shortlist-diff";
import type { CrossDomain } from "@/src/lib/soundcheck";
import { RefineBox } from "./refine-box";
import { RunHistory } from "./run-history";
import { RunOutcome } from "./run-outcome";

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
}

export function RefinableRun({ venueId, venueName, chain: initial, crossDomain }: Props) {
  const [chain, setChain] = useState(initial);
  const [selected, setSelected] = useState(initial.length - 1);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Reason | null>(null);

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
          window.history.replaceState(null, "", `?run=${encodeURIComponent(next.runId)}`);
        },
        (err: unknown) => setError(err instanceof ApiError ? err.reason : "unavailable"),
      )
      .finally(() => setPending(false));
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
        >
          {/* Keyed by run: a new run on screen starts with an empty box. */}
          <RefineBox key={run.runId} pending={pending} error={error} onRefine={refine} />
        </RunOutcome>
      </div>
      {chain.length > 1 && (
        <div className="mt-8 lg:mt-0">
          <RunHistory
            chain={chain}
            selected={selected}
            onSelect={(i) => {
              if (pending) return;
              setSelected(i);
              setError(null);
            }}
          />
        </div>
      )}
    </div>
  );
}
