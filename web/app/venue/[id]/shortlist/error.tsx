"use client";

// Error boundary for /venue/[id]/shortlist: the page handles the agent's own refusals
// (AgentError) itself; anything else thrown past it, or by the run components, lands here
// as the unknown state with the venue's crowd profile as the way back.
import { useParams } from "next/navigation";

import { PageError } from "@/src/components/page-error";
import { idSchema } from "@/src/lib/schemas";

export default function ShortlistError({ error, retry }: { error: unknown; retry: () => void }) {
  const { id } = useParams<{ id: string }>();
  const venueId = idSchema.safeParse(id).success ? id : undefined;
  return <PageError error={error} retry={retry} venueId={venueId} />;
}
