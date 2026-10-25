import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { reasonText } from "@/src/lib/api-client";
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
    if (err instanceof AgentError && err.reason === "not_found") notFound();
    const reason = err instanceof AgentError ? err.reason : "unknown";
    return (
      <Shell>
        <Message title="Couldn't load this venue" body={reasonText(reason)} />
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
