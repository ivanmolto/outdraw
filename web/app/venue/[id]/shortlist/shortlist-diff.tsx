// How a refined shortlist differs from its parent's: a badge on each act that entered
// (green) or moved (an arrow and the number of places), and the acts that left, greyed out
// under the list. No hooks: rendered inside ShortlistView on the server and the client.
import type { ActChange, ShortlistDiff } from "@/src/lib/shortlist-diff";

export function ChangeBadge({ change }: { change?: ActChange }) {
  if (!change || change.kind === "same") return null;
  if (change.kind === "entered") {
    return (
      <span
        data-testid="change"
        data-change="entered"
        className="shrink-0 rounded-full border border-emerald-700 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800"
      >
        New
      </span>
    );
  }
  const up = change.by > 0;
  const places = Math.abs(change.by);
  return (
    <span
      data-testid="change"
      data-change="moved"
      title={`Was #${change.from}`}
      className="shrink-0 rounded-full border border-ink/30 px-2 py-0.5 font-mono text-xs text-ink"
    >
      <span aria-hidden="true">
        {up ? "↑" : "↓"}
        {places}
      </span>
      <span className="sr-only">
        Moved {up ? "up" : "down"} {places} {places === 1 ? "place" : "places"} from #{change.from}
      </span>
    </span>
  );
}

export function LeftList({ diff }: { diff?: ShortlistDiff }) {
  if (!diff || diff.left.length === 0) return null;
  return (
    <section aria-label="Left the list" className="mt-4">
      <h2 className="text-sm font-medium text-ink/50">Left the list</h2>
      <ul className="mt-2 space-y-2">
        {diff.left.map(({ entry, rank }) => (
          <li
            key={entry.entity_id}
            data-change="left"
            className="flex items-baseline gap-3 rounded-lg border border-ink/10 bg-ink/5 px-4 py-2 text-sm text-ink/45"
          >
            <span className="shrink-0 font-mono" aria-hidden="true">
              {rank}
            </span>
            <span className="min-w-0 flex-1 break-words line-through">{entry.name}</span>
            <span className="shrink-0 font-mono">{entry.total.toFixed(2)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
