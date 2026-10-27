// The Qloo / Draw badge on every Soundcheck item, from the agent's `source_labels`.
import type { SourceLabel } from "@/types/schemas";

export function Provenance({ source }: { source: SourceLabel | undefined }) {
  if (source === "qloo") {
    return (
      <span
        data-testid="source"
        data-source="qloo"
        title="Qloo data"
        className="inline-block shrink-0 rounded-sm bg-stage px-1 py-px text-[10px] font-semibold tracking-wide text-brand-amber uppercase"
      >
        Qloo
      </span>
    );
  }
  if (source === "draw") {
    return (
      <span
        data-testid="source"
        data-source="draw"
        title="Outdraw's interpretation"
        className="inline-block shrink-0 rounded-sm border border-ink/30 px-1 py-px text-[10px] font-semibold tracking-wide text-ink/70 uppercase"
      >
        Draw
      </span>
    );
  }
  // The agent validates every trail as fully labelled; say so plainly if one slips through.
  return (
    <span
      data-testid="source"
      data-source="none"
      className="inline-block shrink-0 rounded-sm border border-dashed border-ink/40 px-1 py-px text-[10px] text-ink/60"
    >
      Unlabelled
    </span>
  );
}
