// What a finished run shows: the ranked list, the agent's question when it stopped to ask,
// or a generic failure. Rendered by RefinableRun for a stored run and a fresh one; `children`
// (the refinement box) goes under whichever it shows.
import Link from "next/link";
import type { ReactNode } from "react";

import { reasonText } from "@/src/lib/api-client";
import type { PlayedHereMap } from "@/src/lib/played-here";
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
  played,
  profileHref = `/venue/${encodeURIComponent(venueId)}`,
  children,
}: {
  venueId: string;
  venueName: string;
  run: ParsedRun;
  crossDomain?: CrossDomain;
  diff?: ShortlistDiff;
  played?: PlayedHereMap;
  profileHref?: string;
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
        played={played}
        profileHref={profileHref}
      >
        {children}
      </ShortlistView>
    );
  }
  const back = (
    <Link href={profileHref} className="mt-4 inline-block text-sm font-medium underline">
      Back to crowd profile
    </Link>
  );
  if (run.kind === "ask") {
    return (
      <>
        <div role="alert" className="rounded-card border border-line bg-surface p-6">
          <h1 className="display text-2xl">Outdraw needs a choice first</h1>
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
  if (run.empty) {
    // The search came back empty, not a fault: the agent's notes say what filtered
    // everything out, and the next refinement starts from the last run that ranked.
    return (
      <>
        <div role="alert" className="rounded-card border border-line bg-surface p-6">
          <h1 className="display text-2xl">No acts survived that change</h1>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink/70">
            {run.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
          <p className="mt-3 text-sm text-ink/70">
            Ask for something else below; it starts again from the last shortlist that ranked.
          </p>
          {back}
        </div>
        {children}
      </>
    );
  }
  return (
    <>
      <div role="alert" className="rounded-card border border-line bg-surface p-6">
        <h1 className="display text-2xl">Couldn&apos;t build the shortlist</h1>
        <p className="mt-2 text-sm text-ink/70">{reasonText("unknown")}</p>
        {back}
      </div>
      {children}
    </>
  );
}
