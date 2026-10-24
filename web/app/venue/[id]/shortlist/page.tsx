import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { reasonText } from "@/src/lib/api-client";
import { defaultConstraints, parseRun } from "@/src/lib/run-result";
import { idSchema } from "@/src/lib/schemas";
import { crossDomain } from "@/src/lib/soundcheck";
import { AgentError, agent, type RunOut, type VenueOut } from "@/src/server/agent";
import { Message, Shell } from "../shell";
import { RunOutcome } from "./run-outcome";
import { RunShortlist } from "./run-shortlist";

export const metadata: Metadata = {
  title: "Shortlist · Outdraw",
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

/** /venue/[id]/shortlist: the stored run from `?run=`, or a fresh run with the defaults. */
export default async function ShortlistPage({
  params,
  searchParams,
}: PageProps<"/venue/[id]/shortlist">) {
  const { id } = await params;
  if (!idSchema.safeParse(id).success) notFound();

  let venue: VenueOut;
  let run: RunOut | null;
  try {
    venue = await agent.getVenue(id);
    run = await storedRun(id, (await searchParams).run);
  } catch (err) {
    if (err instanceof AgentError && err.reason === "not_found") notFound();
    const reason = err instanceof AgentError ? err.reason : "unknown";
    return (
      <Shell>
        <Message title="Couldn't load this shortlist" body={reasonText(reason)} />
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
      {run ? (
        <RunOutcome
          venueId={venue.id}
          venueName={venue.venue.name}
          run={parseRun(run)}
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
