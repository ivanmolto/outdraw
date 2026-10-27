"use client";

// Error boundary for /v/[slug]: a failed seed read (the agent down) or a crash in the
// precomputed sections shows the unknown state with a retry.
import { PageError } from "@/src/components/page-error";

export default function DemoVenueError({ error, retry }: { error: unknown; retry: () => void }) {
  return <PageError error={error} retry={retry} />;
}
