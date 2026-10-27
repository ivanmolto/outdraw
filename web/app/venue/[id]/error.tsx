"use client";

// Error boundary for /venue/[id]: the page handles the agent's own refusals (AgentError)
// itself; anything else thrown past it lands here as the unknown state.
import { useParams } from "next/navigation";

import { PageError } from "@/src/components/page-error";
import { idSchema } from "@/src/lib/schemas";

export default function VenueError({ error, retry }: { error: unknown; retry: () => void }) {
  const { id } = useParams<{ id: string }>();
  const venueId = idSchema.safeParse(id).success ? id : undefined;
  return <PageError error={error} retry={retry} venueId={venueId} />;
}
