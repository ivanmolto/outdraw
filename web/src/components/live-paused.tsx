// What a step that needs live analysis shows when it is refused (the web's rate limit, the
// agent's daily budget or LIVE_MODE=off, all alike; `sampleSeed` in src/lib/api-client.ts):
// a calm notice with a way to a precomputed demo, never an error. Nothing the booker entered
// is lost; the step works again once live analysis is back.
import Link from "next/link";

import type { SeedSlug } from "@/src/lib/seeds";

export const PAUSED_TITLE = "Live analysis is busy right now";

export function LivePaused({
  slug,
  children,
  className = "",
}: {
  slug: SeedSlug;
  /** What can't happen right now, and what is kept. */
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label="Live analysis paused"
      className={`rounded-card border border-dashed border-ink/40 p-4 text-sm ${className}`}
    >
      <p className="font-medium text-ink">{PAUSED_TITLE}</p>
      <p className="mt-1 text-ink/70">{children}</p>
      <Link href={`/v/${encodeURIComponent(slug)}`} className="mt-2 inline-block font-medium underline">
        See a sample shortlist
      </Link>
    </div>
  );
}
