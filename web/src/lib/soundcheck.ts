// The Soundcheck panel's reading of a candidate's evidence trail. The agent stores one
// `EvidenceTrail` per ranked candidate (agent/draw/ranking/evidence.py) with a source label
// per item; label keys name the item: a sub-score by its bare name, `total`,
// `weight:<sub-score>`, `input:<ref>`, `call:<call_id>`, `affinity:<sub-score>`.
import { z } from "zod";

import type {
  Affinity,
  EvidenceTrail,
  ProfileItem,
  QlooCallRecord,
  QlooStatus,
  QlooWorkflow,
  SourceLabel,
} from "@/types/schemas";
import { SUB_SCORES, type SubScore } from "./run-result";

const subScore = z.enum(SUB_SCORES);
const sourceLabel = z.enum(["qloo", "draw"]);
const unit = z.number().min(0).max(1);

const breakdown = z.object({
  crowd_affinity: unit,
  past_fit: unit,
  local_heat: unit,
  novelty: unit,
  size_fit: unit,
  momentum: unit.optional(),
});
const weights = breakdown.extend({
  crowd_affinity: z.number().min(0),
  past_fit: z.number().min(0),
  local_heat: z.number().min(0),
  novelty: z.number().min(0),
  size_fit: z.number().min(0),
  momentum: z.number().min(0).optional(),
});

/** The stored trail as the evidence route returns it: the `EvidenceTrail` (T04 schema) plus
 * the candidate's ids. The agent types `Evidence.evidence` as a bare object, so the
 * soundcheck route parses it with this before the panel sees it. Loose on unknown keys:
 * fields the agent adds later pass through. */
export const candidateTrailSchema = z.object({
  candidate_id: z.string().min(1),
  run_id: z.string().min(1),
  entity_id: z.string().min(1),
  name: z.string().min(1),
  top_reason: z.string(),
  neutral: z.array(subScore),
  // Trails stored before T22 (24 Oct 2026) carry neither inputs nor affinities, and label
  // only the sub-scores: they still open, with those branches empty and the rest unlabelled.
  inputs: z.array(z.object({ ref: z.string().min(1), label: z.string() })).default([]),
  qloo_calls: z.array(
    z.object({
      call_id: z.string().min(1),
      workflow: z.enum([
        "find_tags",
        "describe",
        "recommend",
        "rank",
        "where_popular",
        "compare_audiences",
        "audience_demographics",
        "trends",
      ]),
      // Also absent from pre-T22 records.
      params_summary: z.string().default(""),
      status: z.enum(["ok", "empty", "needs_input", "partial", "degraded", "error"]),
      cache_hit: z.boolean(),
    }),
  ),
  affinities: z
    .array(z.object({ entity: z.string().min(1), value: unit, call_id: z.string().min(1) }))
    .default([]),
  sub_scores: breakdown,
  weights,
  total: z.number(),
  source_labels: z.record(z.string(), sourceLabel),
}) satisfies z.ZodType<EvidenceTrail & { neutral: SubScore[] }>;

export type CandidateTrail = z.infer<typeof candidateTrailSchema>;

/** What `/api/runs/[id]/candidates/[entityId]/soundcheck` answers: the trail, the
 * envelope summary of each of its calls that came back less than ok (by call_id), and
 * whether the booker has pencilled the act. */
export interface SoundcheckData {
  trail: CandidateTrail;
  summaries: Record<string, string>;
  pencilled: boolean;
}

/** A profile entity as both the schema and the API contract type it (affinity may be null). */
export type CrossDomainItem = Omit<ProfileItem, "affinity"> & { affinity?: number | null };

/** The crowd's taste outside music: the profile lists the strip draws from. */
export interface CrossDomain {
  places: CrossDomainItem[];
  films: CrossDomainItem[];
  brands: CrossDomainItem[];
}

/** Just the strip's lists, so a client component isn't handed the whole profile. */
export const crossDomain = (profile: CrossDomain): CrossDomain => ({
  places: profile.places,
  films: profile.films,
  brands: profile.brands,
});

/** Statuses whose envelope summary the panel shows next to the call. */
export const SUMMARISED: ReadonlySet<QlooStatus> = new Set([
  "partial",
  "degraded",
  "empty",
  "error",
]);

export const REF = {
  total: "total",
  weight: (name: SubScore) => `weight:${name}`,
  input: (ref: string) => `input:${ref}`,
  call: (callId: string) => `call:${callId}`,
  affinity: (name: SubScore) => `affinity:${name}`,
  /** Draw's templated wording of the score. */
  topReason: "top_reason",
  /** One of the crowd profile's cross-domain lists. */
  profile: (kind: keyof CrossDomain) => `profile:${kind}`,
} as const;

/** The label for `key`, or undefined when the trail left the item unlabelled. */
export const labelOf = (trail: EvidenceTrail, key: string): SourceLabel | undefined =>
  trail.source_labels[key];

// Which sub-score a call's workflow measures (`WORKFLOW_OF` in agent/draw/ranking/score.py;
// `rank` is Qloo's affinity for a user-supplied shortlist).
const SUB_OF_WORKFLOW: Partial<Record<QlooWorkflow, SubScore>> = {
  recommend: "crowd_affinity",
  rank: "crowd_affinity",
  compare_audiences: "past_fit",
  where_popular: "local_heat",
  trends: "momentum",
};

/** The sub-score an affinity backs, named by the workflow of the call it came from. */
export function affinitySubScore(
  calls: QlooCallRecord[],
  affinity: Affinity,
): SubScore | undefined {
  const call = calls.find((c) => c.call_id === affinity.call_id);
  return call ? SUB_OF_WORKFLOW[call.workflow] : undefined;
}

/** A neutral fallback as the user reads it: `0.5`, `0.62`. */
export const formatNeutral = (value: number) => String(Number(value.toFixed(2)));
