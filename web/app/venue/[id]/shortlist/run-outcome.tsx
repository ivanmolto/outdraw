// What a finished run shows: the ranked list, the agent's question when it stopped to ask,
// or a generic failure. Rendered by RefinableRun for a stored run and a fresh one; `children`
// (the refinement box) goes under whichever it shows.
import Link from "next/link";
import type { ReactNode } from "react";

import { reasonText } from "@/src/lib/api-client";
import type { ParsedRun } from "@/src/lib/run-result";
import type { ShortlistDiff } from "@/src/lib/shortlist-diff";
import type { CrossDomain } from "@/src/lib/soundcheck";
import { ShortlistView } from "./shortlist-view";

export function RunOutcome({
  venueId,
  venueName,
  run,
  crossDomain,
  diff,
  children,
}: {
  venueId: string;
  venueName: string;
  run: ParsedRun;
  crossDomain?: CrossDomain;
  diff?: ShortlistDiff;
  children?: ReactNode;
}) {
  if (run.kind === "shortlist") {
    return (
      <ShortlistView
        venueId={venueId}
        venueName={venueName}
        runId={run.runId}
        result={run}
        crossDomain={crossDomain}
        diff={diff}
      >
        {children}
      </ShortlistView>
    );
  }
  const back = (
    <Link
      href={`/venue/${encodeURIComponent(venueId)}`}
      className="mt-4 inline-block text-sm font-medium underline"
    >
      Back to crowd profile
    </Link>
  );
  if (run.kind === "ask") {
    return (
      <>
        <div role="alert" className="rounded-lg border border-ink/15 bg-white/60 p-6">
          <h1 className="text-xl font-semibold tracking-tight">Outdraw needs a choice first</h1>
          <p className="mt-2 text-sm text-ink/70">{run.question}</p>
          {run.options.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-sm text-ink/70">
              {run.options.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          )}
          {back}
        </div>
        {children}
      </>
    );
  }
  return (
    <>
      <div role="alert" className="rounded-lg border border-ink/15 bg-white/60 p-6">
        <h1 className="text-xl font-semibold tracking-tight">Couldn&apos;t build the shortlist</h1>
        <p className="mt-2 text-sm text-ink/70">{reasonText("unknown")}</p>
        {back}
      </div>
      {children}
    </>
  );
}
