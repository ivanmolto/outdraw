"use client";

// A fresh run for /venue/[id]/shortlist: POST /api/runs with the default constraints, the
// skeleton while the agent works (up to ~2 minutes), then the ranked list. The run id goes
// into the URL (`?run=`), so a reload or a shared link reads the stored run instead of
// spending Qloo calls on a new one.
import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api, reasonText, type Reason } from "@/src/lib/api-client";
import { parseRun, type ParsedRun } from "@/src/lib/run-result";
import type { Constraints } from "@/types/schemas";
import { ShortlistSkeleton } from "./shortlist-view";
import { RunOutcome } from "./run-outcome";

type State =
  | { name: "running" }
  | { name: "done"; run: ParsedRun }
  | { name: "failed"; reason: Reason };

/** Reasons a retry can fix; the rest need the user to do something else first. */
const RETRYABLE: ReadonlySet<Reason> = new Set(["busy", "unavailable", "unknown"]);

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
}

export function RunShortlist({ venueId, venueName, constraints }: Props) {
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
        if (active) setState({ name: "failed", reason: err instanceof ApiError ? err.reason : "unavailable" });
      },
    );
    return () => {
      active = false;
    };
  }, [venueId, constraints, attempt]);

  if (state.name === "running") {
    return (
      <>
        <ShortlistSkeleton label="Building shortlist" />
        <p className="mt-4 text-sm text-ink/60" aria-live="polite">
          Outdraw is asking Qloo for acts and scoring them. This can take up to two minutes.
        </p>
      </>
    );
  }

  if (state.name === "failed") {
    return (
      <div role="alert" className="rounded-lg border border-ink/15 bg-white/60 p-6">
        <h1 className="text-xl font-semibold tracking-tight">Couldn&apos;t build the shortlist</h1>
        <p className="mt-2 text-sm text-ink/70">{reasonText(state.reason)}</p>
        <div className="mt-4 flex flex-wrap items-center gap-4">
          {RETRYABLE.has(state.reason) && (
            <button
              type="button"
              onClick={() => {
                setState({ name: "running" });
                setAttempt((n) => n + 1);
              }}
              className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper"
            >
              Try again
            </button>
          )}
          <Link href={`/venue/${encodeURIComponent(venueId)}`} className="text-sm font-medium underline">
            Back to crowd profile
          </Link>
        </div>
      </div>
    );
  }

  return <RunOutcome venueId={venueId} venueName={venueName} run={state.run} />;
}
