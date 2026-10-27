// The run-history sidebar: the runs of this parent chain, first run to latest refinement,
// each with the booker's message and the agent's explanation of what changed. Picking one
// shows that run with its diff against its own parent.
import type { ParsedRun } from "@/src/lib/run-result";
import { SourceBadge } from "../profile-view";

const outcome = (run: ParsedRun) =>
  run.kind === "shortlist"
    ? `${run.shortlist.length} ${run.shortlist.length === 1 ? "act" : "acts"}`
    : run.kind === "ask"
      ? "Needs a choice"
      : "Failed";

export function RunHistory({
  chain,
  selected,
  onSelect,
}: {
  chain: readonly ParsedRun[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  return (
    <aside aria-label="Run history" className="min-w-0">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink/60">Run history</h2>
        <SourceBadge source="outdraw" />
      </div>
      <ol className="mt-3 space-y-2">
        {chain.map((run, i) => {
          const current = i === selected;
          return (
            <li key={run.runId}>
              <button
                type="button"
                aria-current={current ? "true" : undefined}
                onClick={() => onSelect(i)}
                className={`w-full rounded-card border p-3 text-left text-sm ${
                  current ? "border-ink bg-surface" : "border-line bg-surface hover:border-ink/40"
                }`}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold text-ink">
                    <span className="font-mono text-ink/50">{i + 1}</span>{" "}
                    {run.refinement ? "Refined" : "First run"}
                  </span>
                  <span className="shrink-0 text-xs text-ink/50">{outcome(run)}</span>
                </span>
                {run.refinement ? (
                  <>
                    <span className="mt-1 block break-words text-ink/80">
                      &ldquo;{run.refinement.message}&rdquo;
                    </span>
                    <span className="mt-1 block break-words text-xs text-ink/60">
                      {run.refinement.explanation}
                    </span>
                  </>
                ) : (
                  <span className="mt-1 block text-xs text-ink/60">
                    Default constraints for this venue.
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
