"use client";

// Error boundary for /venue/new: a crash in the form or the confirm step shows the unknown
// state with a retry; the draft stays in sessionStorage (new-venue.tsx).
import { PageError } from "@/src/components/page-error";

export default function NewVenueError({ error, retry }: { error: unknown; retry: () => void }) {
  return <PageError error={error} retry={retry} />;
}
