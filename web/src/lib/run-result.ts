// The shortlist page's reading of a run: default constraints for the first run, and the
// typed result (`RunResult` from the agent's contract) sorted into what the page shows:
// a shortlist, the agent's question, or a failure. Order is the agent's: it comes from the
// deterministic score, so nothing here sorts.
import type { RunOut } from "@/types/agent";
import type { components } from "@/types/api";
import type { Constraints } from "@/types/schemas";

export type ShortlistEntry = components["schemas"]["ShortlistEntry"];
export type ShortlistResult = Omit<
  components["schemas"]["RunResult"],
  "shortlist" | "notes" | "ask" | "error" | "refinement"
> & { shortlist: ShortlistEntry[]; notes: string[] };

/** Capacity bounds for the size tiers: < 300 emerging, < 1,500 mid, else established. */
const TIER_CAPACITY = { emerging: 300, mid: 1_500 } as const;

export type SizeBand = Constraints["size_tier"];

/** The size tiers as capacity bands, smallest first: the rooms each tier suits. */
export const SIZE_BANDS: { band: SizeBand; label: string }[] = [
  { band: "emerging", label: `Emerging (rooms under ${TIER_CAPACITY.emerging})` },
  {
    band: "mid",
    label: `Mid (rooms of ${TIER_CAPACITY.emerging} to ${TIER_CAPACITY.mid.toLocaleString("en-US")})`,
  },
  {
    band: "established",
    label: `Established (rooms of ${TIER_CAPACITY.mid.toLocaleString("en-US")} and up)`,
  },
];

/** The constraints a first shortlist runs with: no tag filters, a size tier that suits
 * the room, acts from the last six months left out. */
export function defaultConstraints(capacity: number): Constraints {
  return {
    include_tags: [],
    exclude_tags: [],
    size_tier:
      capacity < TIER_CAPACITY.emerging
        ? "emerging"
        : capacity < TIER_CAPACITY.mid
          ? "mid"
          : "established",
    exclude_recent_months: 6,
    max_candidates: 40,
  };
}

/** Sub-scores in display order (the order of `WEIGHTS` in agent/draw/ranking/score.py). */
export const SUB_SCORES = [
  "crowd_affinity",
  "past_fit",
  "local_heat",
  "novelty",
  "size_fit",
  "momentum",
] as const;
export type SubScore = (typeof SUB_SCORES)[number];

export const SUB_SCORE_LABELS: Record<SubScore, string> = {
  crowd_affinity: "Crowd affinity",
  past_fit: "Past fit",
  local_heat: "Local heat",
  novelty: "Novelty",
  size_fit: "Size fit",
  momentum: "Momentum",
};

/** How a refined run came from its parent: the booker's message and the agent's
 * one-sentence explanation of what changed. */
export interface RunLineage {
  parentRunId: string | null;
  refinement: { message: string; explanation: string } | null;
}

export type ParsedRun = RunLineage &
  (
    | ({ kind: "shortlist"; runId: string } & ShortlistResult)
    | { kind: "ask"; runId: string; question: string; options: string[] }
    | { kind: "error"; runId: string }
  );

export function parseRun(run: RunOut): ParsedRun {
  const runId = run.run_id;
  const result = run.result;
  const refinement = result?.refinement;
  const lineage: RunLineage = {
    parentRunId: run.parent_run_id ?? null,
    refinement: refinement
      ? { message: refinement.message, explanation: refinement.explanation }
      : null,
  };
  if (!result || result.error) return { kind: "error", runId, ...lineage };
  if (run.status === "ask" && result.ask) {
    const options = result.ask.candidates.flatMap((c) =>
      typeof c.name === "string" ? [c.name] : [],
    );
    return { kind: "ask", runId, question: result.ask.question, options, ...lineage };
  }
  if (run.status === "ok" && result.shortlist) {
    return {
      kind: "shortlist",
      runId,
      ...lineage,
      shortlist: result.shortlist,
      notes: result.notes ?? [],
      data_quality: result.data_quality,
      weights: result.weights,
      stop_reason: result.stop_reason,
      steps: result.steps,
      profile_id: result.profile_id,
    };
  }
  return { kind: "error", runId, ...lineage };
}
