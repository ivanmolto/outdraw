"use client";

// Error boundary for the landing page and /venue/new (the segments without one of their
// own): nothing thrown past a page reaches the browser as text.
import { PageError } from "@/src/components/page-error";

export default function RootError({ error, retry }: { error: unknown; retry: () => void }) {
  return <PageError error={error} retry={retry} />;
}
