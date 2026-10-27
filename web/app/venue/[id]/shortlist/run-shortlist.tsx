"use client";

// A fresh run for /venue/[id]/shortlist: POST /api/runs with the default constraints, the
// skeleton while the agent works (up to ~2 minutes), then the ranked list. The run id goes
// into the URL (`?run=`), so a reload or a shared link reads the stored run instead of
// spending Qloo calls on a new one. A run refused for want of live analysis (busy, budget,
// LIVE_MODE=off) shows the closest seed's precomputed sample instead of an error.
import { useEffect, useState } from "react";

import { ErrorState } from "@/src/components/error-state";
import { Elapsed, Spinner } from "@/src/components/progress";
import { api, errorOf, sampleSeed, type Reason } from "@/src/lib/api-client";
import type { ErrorDetails } from "@/src/lib/error-states";
import { parseRun, type ParsedRun } from "@/src/lib/run-result";
import type { SeedSlug } from "@/src/lib/seeds";
import type { CrossDomain } from "@/src/lib/soundcheck";
import type { Constraints } from "@/types/schemas";
import { ShortlistSkeleton } from "./shortlist-view";
import { RefinableRun } from "./refinable-run";
import { SampleFallback } from "./sample-fallback";

type State =
  | { name: "running" }
  | { name: "done"; run: ParsedRun }
  | { name: "sample"; slug: SeedSlug }
  | { name: "failed"; reason: Reason; details: ErrorDetails };

/** In-flight runs by venue: Strict Mode mounts effects twice, and a run costs Qloo quota. */
const inFlight = new Map<string, Promise<ParsedRun>>();

function startRun(venueId: string, constraints: Constraints): Promise<ParsedRun> {
  let run = inFlight.get(venueId);
  if (!run) {
    run = api
      .createRun({ venue_id: venueId, constraints, message: "" })
      .then(parseRun)
      .finally(() => inFlight.delete(venueId));
    inFlight.set(venueId, run);
  }
  return run;
}

interface Props {
  venueId: string;
  venueName: string;
  constraints: Constraints;
  crossDomain?: CrossDomain;
}

export function RunShortlist({ venueId, venueName, constraints, crossDomain }: Props) {
  const [state, setState] = useState<State>({ name: "running" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    startRun(venueId, constraints).then(
      (run) => {
        if (!active) return;
        window.history.replaceState(null, "", `?run=${encodeURIComponent(run.runId)}`);
        setState({ name: "done", run });
      },
      (err: unknown) => {
        if (!active) return;
        const slug = sampleSeed(err);
        setState(slug ? { name: "sample", slug } : { name: "failed", ...errorOf(err) });
      },
    );
    return () => {
      active = false;
    };
  }, [venueId, constraints, attempt]);

  if (state.name === "running") {
    return (
      <>
        <div className="mb-6 flex items-center gap-3 rounded-lg border border-ink/15 bg-white p-4 text-sm">
          <Spinner className="text-ink" />
          <div className="min-w-0">
            <p className="text-ink" aria-live="polite">
              Outdraw is asking Qloo for acts and scoring them. This can take up to two minutes.
            </p>
            <Elapsed className="mt-1 block text-ink/50" />
          </div>
        </div>
        <ShortlistSkeleton label="Building shortlist" />
      </>
    );
  }

  const retry = () => {
    setState({ name: "running" });
    setAttempt((n) => n + 1);
  };

  const failed = (reason: Reason, details: ErrorDetails = {}) => (
    <ErrorState reason={reason} details={{ venueId, ...details }} onRetry={retry} />
  );

  if (state.name === "failed") return failed(state.reason, state.details);

  if (state.name === "sample") {
    return (
      <SampleFallback
        slug={state.slug}
        action={
          <button type="button" onClick={retry} className="font-medium underline">
            Try a live run again
          </button>
        }
        fallback={failed("busy", { fallbackSeed: state.slug })}
      />
    );
  }

  return (
    <RefinableRun
      venueId={venueId}
      venueName={venueName}
      chain={[state.run]}
      crossDomain={crossDomain}
    />
  );
}
