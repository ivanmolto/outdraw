import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ErrorState } from "@/src/components/error-state";
import { defaultConstraints, parseRun, type ParsedRun } from "@/src/lib/run-result";
import { idSchema } from "@/src/lib/schemas";
import { crossDomain } from "@/src/lib/soundcheck";
import { AgentError, agent, type RunOut, type VenueOut } from "@/src/server/agent";
import { Message, Shell } from "../shell";
import { RefinableRun } from "./refinable-run";
import { RunShortlist } from "./run-shortlist";

export const metadata: Metadata = {
  title: "Shortlist",
  description: "Acts your crowd will turn out for, ranked by Outdraw's score.",
};

/** A stored run of this venue named by `?run=`, or null to start a fresh one. */
async function storedRun(venueId: string, runId: unknown): Promise<RunOut | null> {
  if (typeof runId !== "string" || !idSchema.safeParse(runId).success) return null;
  try {
    const run = await agent.getRun(runId);
    return run.venue_id === venueId ? run : null;
  } catch (err) {
    if (err instanceof AgentError && err.reason === "not_found") return null;
    throw err;
  }
}

/** Runs of the history sidebar beyond this many parents back are left out. */
const MAX_CHAIN = 12;

/** `run` and its parents, oldest first: plain reads, no Qloo calls. A parent that is gone
 * or belongs to another venue ends the chain there. */
async function runChain(run: RunOut): Promise<ParsedRun[]> {
  const chain = [parseRun(run)];
  let parentId = run.parent_run_id;
  while (parentId && chain.length < MAX_CHAIN) {
    const parent = await storedRun(run.venue_id, parentId);
    if (!parent) break;
    chain.unshift(parseRun(parent));
    parentId = parent.parent_run_id;
  }
  return chain;
}

/** /venue/[id]/shortlist: the stored run from `?run=`, or a fresh run with the defaults. */
export default async function ShortlistPage({
  params,
  searchParams,
}: PageProps<"/venue/[id]/shortlist">) {
  const { id } = await params;
  if (!idSchema.safeParse(id).success) notFound();

  let venue: VenueOut;
  let chain: ParsedRun[] | null;
  try {
    venue = await agent.getVenue(id);
    const run = await storedRun(id, (await searchParams).run);
    chain = run && (await runChain(run));
  } catch (err) {
    // The agent's own refusals are states of this page; anything else is ours (a bug) and
    // goes to ./error.tsx, reported by instrumentation.ts.
    if (!(err instanceof AgentError)) throw err;
    if (err.reason === "not_found") notFound();
    return (
      <Shell>
        <ErrorState
          reason={err.reason}
          details={{ venueId: id, pending: err.pending }}
          retryHref={`/venue/${encodeURIComponent(id)}/shortlist`}
        />
      </Shell>
    );
  }

  if (!venue.profile) {
    return (
      <Shell>
        <Message
          title="No crowd profile yet"
          body={`Confirm the Qloo matches for ${venue.venue.name} to build its crowd profile, then its shortlist.`}
        />
      </Shell>
    );
  }

  const strip = crossDomain(venue.profile);
  return (
    <Shell wide>
      {chain ? (
        <RefinableRun
          venueId={venue.id}
          venueName={venue.venue.name}
          chain={chain}
          crossDomain={strip}
        />
      ) : (
        <RunShortlist
          venueId={venue.id}
          venueName={venue.venue.name}
          constraints={defaultConstraints(venue.venue.capacity)}
          crossDomain={strip}
        />
      )}
    </Shell>
  );
}
