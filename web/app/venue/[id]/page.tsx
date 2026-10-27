import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ErrorState } from "@/src/components/error-state";
import { idSchema } from "@/src/lib/schemas";
import { AgentError, agent, type VenueOut } from "@/src/server/agent";
import { ProfileView } from "./profile-view";
import { Message, Shell } from "./shell";

export const metadata: Metadata = {
  title: "Crowd profile",
  description: "What your crowd likes, from Qloo's taste graph.",
};

/** The stored venue and its profile; streams behind ./loading.tsx. */
export default async function VenuePage({ params }: PageProps<"/venue/[id]">) {
  const { id } = await params;
  if (!idSchema.safeParse(id).success) notFound();

  let venue: VenueOut;
  try {
    venue = await agent.getVenue(id);
  } catch (err) {
    // The agent's own refusals are states of this page; anything else is ours (a bug) and
    // goes to ./error.tsx, reported by instrumentation.ts.
    if (!(err instanceof AgentError)) throw err;
    if (err.reason === "not_found") notFound();
    return (
      <Shell>
        <ErrorState reason={err.reason} retryHref={`/venue/${encodeURIComponent(id)}`} />
      </Shell>
    );
  }

  if (!venue.profile) {
    return (
      <Shell>
        <Message
          title="No crowd profile yet"
          body={`Confirm the Qloo matches for ${venue.venue.name} to build its crowd profile.`}
        />
      </Shell>
    );
  }

  return (
    <Shell>
      <ProfileView venueId={venue.id} venueName={venue.venue.name} profile={venue.profile} />
    </Shell>
  );
}
