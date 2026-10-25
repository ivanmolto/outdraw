// Input validation for the /api proxy routes, shared with the client forms (T18).
// Bounds are tighter than the JSON Schemas in /shared: they reject junk before it costs a
// Qloo call or Claude tokens. `satisfies` keeps each schema's output assignable to the
// generated type the agent expects.
import { z } from "zod";

import type { Constraints, PastShow, VenueInput } from "@/types/schemas";

export const LIMITS = {
  name: 80,
  city: 60,
  country: 60,
  act: 80,
  message: 300,
  pastShows: 10,
  tags: 20,
  tag: 128,
  capacity: 100_000,
  ticketPrice: 10_000,
  gross: 10_000_000,
  recentMonths: 60,
  maxCandidates: 50,
  /** Ids in paths and bodies: `ven_…`, `run_…`, `urn:entity:…`. */
  id: 128,
  /** A resolution key is `venue:<name>` or an act name. */
  query: 100,
} as const;

/** Trimmed, non-empty, at most `max` characters. */
const text = (max: number) => z.string().trim().min(1).max(max);

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
  message: z.string().trim().max(LIMITS.message).default(""),
});

/** POST /api/runs/{id}/refine: what to change, in the booker's words. */
export const refineSchema = z.strictObject({ message: text(LIMITS.message) });

/** PATCH /api/runs/{id}/candidates/{entityId}: pencil (or release) a hold on the act. */
export const pencilSchema = z.strictObject({ pencilled: z.boolean() });

/** Path segments: venue, run and entity ids. */
export const idSchema = id;

export type VenueUpsert = z.infer<typeof venueUpsertSchema>;
export type ConfirmInput = z.infer<typeof confirmSchema>;
export type RunInput = z.infer<typeof runSchema>;
export type RefineInput = z.infer<typeof refineSchema>;
export type PencilInput = z.infer<typeof pencilSchema>;
