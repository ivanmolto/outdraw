"use client";

// The "Precomputed demo" label in the shortlist header, shown while the seed run is on
// screen (src/lib/precomputed.tsx says which run that is).
import { usePrecomputed } from "@/src/lib/precomputed";

export function PrecomputedLabel({ runId }: { runId: string }) {
  if (!usePrecomputed(runId)) return null;
  return (
    <span
      data-testid="precomputed-label"
      title="Read from results computed ahead of time: no Qloo or model calls"
      className="inline-block rounded-sm border border-dashed border-ink/40 px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-ink/70 uppercase"
    >
      Precomputed demo
    </span>
  );
}
