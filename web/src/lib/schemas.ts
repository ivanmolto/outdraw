// Input validation for the /api proxy routes, shared with the client forms (T18).
// Bounds are tighter than the JSON Schemas in /shared: they reject junk before it costs a
// Qloo call or Claude tokens; agent/draw/api/inputs.py enforces the same LIMITS again.
// `satisfies` keeps each schema's output assignable to the generated type the agent expects.
import { z } from "zod";

import { SHARED_LIMITS as limits } from "@/types/limits";
import type { Constraints, PastShow, VenueInput } from "@/types/schemas";

/** The bounds from /shared/limits.json (generated into types/limits.ts), the one source
 * both services use. */
export const LIMITS = {
  name: limits.name,
  city: limits.city,
  country: limits.country,
  act: limits.act,
  message: limits.message,
  pastShows: limits.past_shows,
  tags: limits.tags,
  tag: limits.tag,
  capacity: limits.capacity,
  ticketPrice: limits.ticket_price,
  gross: limits.gross,
  recentMonths: limits.recent_months,
  maxCandidates: limits.max_candidates,
  /** Ids in paths and bodies: `ven_…`, `run_…`, `urn:entity:…`. */
  id: limits.id,
  /** A resolution key is `venue:<name>` or an act name. */
  query: limits.query,
} as const;

/** Control characters (Unicode Cc) are dropped, except tab, newline and carriage return,
 * which a booker's message may hold. Same rule as `clean` in agent/draw/api/inputs.py. */
export function clean(value: string): string {
  return value.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, "");
}

/** Cleaned and trimmed, then at most `max` characters, and non-empty unless `min` is 0. */
const text = (max: number, min = 1) => z.string().overwrite(clean).trim().min(min).max(max);

const id = z
  .string()
  .min(1)
  .max(LIMITS.id)
  .regex(/^[A-Za-z0-9:._-]+$/);

export const pastShowSchema = z.strictObject({
  act: text(LIMITS.act),
  date: z.iso.date(),
  tickets_sold: z.int().min(0).max(LIMITS.capacity).optional(),
  capacity: z.int().min(1).max(LIMITS.capacity).optional(),
  ticket_price: z.number().min(0).max(LIMITS.ticketPrice).optional(),
  gross: z.number().min(0).max(LIMITS.gross).optional(),
}) satisfies z.ZodType<PastShow>;

export const venueInputSchema = z.strictObject({
  name: text(LIMITS.name),
  city: text(LIMITS.city),
  country: text(LIMITS.country),
  lat: z.number().min(-90).max(90).optional(),
  lng: z.number().min(-180).max(180).optional(),
  capacity: z.int().min(1).max(LIMITS.capacity),
  past_shows: z.array(pastShowSchema).max(LIMITS.pastShows),
}) satisfies z.ZodType<VenueInput>;

/** POST /api/venues: omit `id` to create, pass one to update that venue. */
export const venueUpsertSchema = venueInputSchema.extend({ id: id.optional() });

/** POST /api/venues/{id}/confirm: `null` means "not in Qloo". */
export const confirmSchema = z.strictObject({
  query: text(LIMITS.query),
  chosen_entity_id: id.nullable(),
});

export const constraintsSchema = z.strictObject({
  include_tags: z.array(text(LIMITS.tag)).max(LIMITS.tags),
  exclude_tags: z.array(text(LIMITS.tag)).max(LIMITS.tags),
  size_tier: z.enum(["emerging", "mid", "established"]),
  exclude_recent_months: z.int().min(0).max(LIMITS.recentMonths),
  max_candidates: z.int().min(1).max(LIMITS.maxCandidates),
}) satisfies z.ZodType<Constraints>;

/** POST /api/runs. */
export const runSchema = z.strictObject({
  venue_id: id,
  constraints: constraintsSchema,
  message: text(LIMITS.message, 0).default(""),
});

/** POST /api/runs/{id}/refine: what to change, in the booker's words. */
export const refineSchema = z.strictObject({ message: text(LIMITS.message) });

/** POST /api/runs/{id}/answer: the Qloo tag chosen for a word the run asked about. */
export const answerSchema = z.strictObject({ for_input: text(LIMITS.query), entity_id: id });

/** PATCH /api/runs/{id}/candidates/{entityId}: pencil (or release) a hold on the act, for
 * a night when `date` is given. */
export const pencilSchema = z.strictObject({
  pencilled: z.boolean(),
  date: z.iso.date().optional(),
});

/** Path segments: venue, run and entity ids. */
export const idSchema = id;

export type VenueUpsert = z.infer<typeof venueUpsertSchema>;
export type ConfirmInput = z.infer<typeof confirmSchema>;
export type RunInput = z.infer<typeof runSchema>;
export type RefineInput = z.infer<typeof refineSchema>;
export type AnswerInput = z.infer<typeof answerSchema>;
export type PencilInput = z.infer<typeof pencilSchema>;
