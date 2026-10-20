// Compile-time checks on the generated types. There is nothing to assert at runtime:
// `satisfies` proves the fixtures fit, `expectTypeOf` pins the enums, and every
// expected-error directive must stay an error. `npm run typecheck` enforces all of it;
// vitest only confirms the module loads.
import { expectTypeOf, test } from "vitest";
import type {
  BookingBrief,
  Candidate,
  Constraints,
  CrowdProfile,
  EvidenceTrail,
  QlooEnvelope,
  QlooStatus,
  ResolvedEntity,
  Score,
  ScoreBreakdown,
  ScoreWeights,
  SourceLabel,
  VenueInput,
} from "./schemas";

const evidence = { item_ref: "genres[0]", call_id: "00000000-0000-0000-0000-000000000001" };
const breakdown = {
  crowd_affinity: 0.8,
  past_fit: 0.6,
  local_heat: 0.5,
  novelty: 0.4,
  size_fit: 0.9,
} satisfies ScoreBreakdown;
const weights = {
  crowd_affinity: 0.35,
  past_fit: 0.2,
  local_heat: 0.2,
  novelty: 0.1,
  size_fit: 0.15,
} satisfies ScoreWeights;

// Valid fixtures: one per schema, plus the compare_audiences envelope shape.
({
  name: "Synthetic Room",
  city: "New York",
  country: "US",
  capacity: 300,
  past_shows: [{ act: "Synthetic Act", date: "2026-05-01", tickets_sold: 280 }],
}) satisfies VenueInput;

({
  status: "error",
  summary: "Qloo rate limit reached.",
  results: [],
  result_count: 0,
  error: { code: "QLOO_RATE_LIMIT", retryable: true, recovery: "Retry later.", layer: "qloo" },
  operation: "find_tags",
}) satisfies QlooEnvelope;

({
  status: "ok",
  results: { tags: [], a: [], b: [], matchEntities: [] },
  result_count: 0,
}) satisfies QlooEnvelope;

({
  query: "synthetic act",
  canonical_name: "Synthetic Act",
  confidence: 0.5,
  candidates: [{ entity_id: "urn:entity:synthetic:1", name: "Synthetic Act", type: "artist" }],
  confirmed: false,
}) satisfies ResolvedEntity;

({
  venue_id: "venue-1",
  signals: [{ entity_id: "urn:entity:synthetic:1", weight: 0.93 }],
  genres: [{ entity_id: "urn:tag:synthetic:genre:1", name: "Synthetic", affinity: 0.7 }],
  related_artists: [],
  places: [],
  films: [],
  brands: [],
  evidence: [evidence],
  data_quality: "ok",
}) satisfies CrowdProfile;

({
  include_tags: [],
  exclude_tags: ["urn:tag:synthetic:genre:2"],
  size_tier: "emerging",
  exclude_recent_months: 6,
  max_candidates: 10,
}) satisfies Constraints;

const candidate = {
  entity_id: "urn:entity:synthetic:2",
  name: "Synthetic Candidate",
  popularity: 0.6,
  affinity: 0.8,
  tags: [],
  evidence: [evidence],
} satisfies Candidate;

({
  total: 0.71,
  breakdown: { ...breakdown, momentum: 0.3 },
  weights,
  top_reason: "Strong overlap with the room's crowd.",
}) satisfies Score;

({
  inputs: [{ ref: "venue", label: "Synthetic Room, 300 cap" }],
  qloo_calls: [
    {
      call_id: evidence.call_id,
      workflow: "recommend",
      params_summary: "artists like 1 signal",
      status: "ok",
      cache_hit: true,
    },
  ],
  affinities: [{ entity: "urn:entity:synthetic:2", value: 0.8, call_id: evidence.call_id }],
  sub_scores: breakdown,
  weights,
  total: 0.71,
  source_labels: { "affinities[0]": "qloo", sub_scores: "draw" },
}) satisfies EvidenceTrail;

({
  why_it_fits: [{ text: "Crowd overlap is high.", evidence_ref: "affinities[0]" }],
  night_format: "Two-band bill, doors 8pm.",
  partners: [],
  outreach_email: { subject: "Booking enquiry", body: "Hi, ..." },
}) satisfies BookingBrief;

// Enums stay closed.
expectTypeOf<QlooStatus>().toEqualTypeOf<
  "ok" | "empty" | "needs_input" | "partial" | "degraded" | "error"
>();
expectTypeOf<SourceLabel>().toEqualTypeOf<"qloo" | "draw">();
expectTypeOf<Constraints["size_tier"]>().toEqualTypeOf<"emerging" | "mid" | "established">();

// Invalid shapes must not compile.
// @ts-expect-error unknown status
({ status: "unknown", results: [], result_count: 0 }) satisfies QlooEnvelope;
// @ts-expect-error capacity is required
({ name: "x", city: "x", country: "x", past_shows: [] }) satisfies VenueInput;
({
  // @ts-expect-error why_it_fits needs at least one reason
  why_it_fits: [],
  night_format: "x",
  partners: [],
  outreach_email: { subject: "x", body: "x" },
}) satisfies BookingBrief;
// @ts-expect-error source labels are qloo or draw only
({ x: "llm" }) satisfies EvidenceTrail["source_labels"];
// @ts-expect-error extra fields are not allowed on closed schemas
({ ...candidate, rank: 1 }) satisfies Candidate;

test("generated types compile (see `npm run typecheck`)", () => {});
