import { describe, expect, it } from "vitest";
import type { z } from "zod";

import {
  LIMITS,
  confirmSchema,
  constraintsSchema,
  idSchema,
  runSchema,
  venueUpsertSchema,
} from "./schemas";

const show = { act: "Synthetic Act", date: "2026-05-01", tickets_sold: 280, capacity: 300 };
const venue = {
  name: "Synthetic Room",
  city: "New York",
  country: "US",
  capacity: 300,
  past_shows: [show],
};
const constraints = {
  include_tags: [],
  exclude_tags: ["urn:tag:synthetic:genre:2"],
  size_tier: "emerging",
  exclude_recent_months: 6,
  max_candidates: 10,
};

/** The dotted path of the first issue, or null when `value` is valid. */
function firstError(schema: z.ZodType, value: unknown): string | null {
  const parsed = schema.safeParse(value);
  return parsed.success ? null : parsed.error.issues[0].path.join(".");
}

describe("venueUpsertSchema", () => {
  it("accepts a full venue, trimming strings", () => {
    const parsed = venueUpsertSchema.parse({ ...venue, name: "  Synthetic Room  ", lat: 40.7 });
    expect(parsed.name).toBe("Synthetic Room");
  });

  it("accepts an update id", () => {
    expect(firstError(venueUpsertSchema, { ...venue, id: "ven_123" })).toBeNull();
  });

  it.each([
    ["empty name", { ...venue, name: "" }, "name"],
    ["whitespace-only name", { ...venue, name: "   " }, "name"],
    ["name too long", { ...venue, name: "x".repeat(LIMITS.name + 1) }, "name"],
    ["city too long", { ...venue, city: "x".repeat(LIMITS.city + 1) }, "city"],
    ["missing country", { ...venue, country: undefined }, "country"],
    ["capacity zero", { ...venue, capacity: 0 }, "capacity"],
    ["capacity fractional", { ...venue, capacity: 1.5 }, "capacity"],
    ["capacity too large", { ...venue, capacity: LIMITS.capacity + 1 }, "capacity"],
    ["capacity as string", { ...venue, capacity: "300" }, "capacity"],
    ["lat out of range", { ...venue, lat: 91 }, "lat"],
    ["lng out of range", { ...venue, lng: -181 }, "lng"],
    ["unknown field", { ...venue, email: "a@b.c" }, ""],
    ["bad id", { ...venue, id: "../etc" }, "id"],
    ["missing past_shows", { ...venue, past_shows: undefined }, "past_shows"],
  ])("rejects %s", (_label, value, field) => {
    expect(firstError(venueUpsertSchema, value)).toBe(field);
  });

  it(`accepts ${LIMITS.pastShows} past shows and rejects ${LIMITS.pastShows + 1}`, () => {
    const shows = (n: number) => Array.from({ length: n }, () => show);
    expect(firstError(venueUpsertSchema, { ...venue, past_shows: shows(10) })).toBeNull();
    expect(firstError(venueUpsertSchema, { ...venue, past_shows: shows(11) })).toBe("past_shows");
  });

  it.each([
    ["empty act", { ...show, act: " " }, "act"],
    ["act too long", { ...show, act: "x".repeat(LIMITS.act + 1) }, "act"],
    ["bad date", { ...show, date: "May 1st" }, "date"],
    ["impossible date", { ...show, date: "2026-02-30" }, "date"],
    ["negative tickets", { ...show, tickets_sold: -1 }, "tickets_sold"],
    ["negative price", { ...show, ticket_price: -5 }, "ticket_price"],
    ["gross too large", { ...show, gross: LIMITS.gross + 1 }, "gross"],
    ["unknown field", { ...show, promoter: "x" }, ""],
  ])("rejects a past show with %s", (_label, value, field) => {
    expect(firstError(venueUpsertSchema, { ...venue, past_shows: [value] })).toBe(
      ["past_shows", "0", field].filter(Boolean).join("."),
    );
  });
});

describe("confirmSchema", () => {
  it("accepts a pick and a skip", () => {
    const pick = { query: "venue:Synthetic Room", chosen_entity_id: "urn:entity:x:1" };
    expect(firstError(confirmSchema, pick)).toBeNull();
    expect(firstError(confirmSchema, { query: "Synthetic Act", chosen_entity_id: null })).toBeNull();
  });

  it.each([
    ["missing choice", { query: "x" }, "chosen_entity_id"],
    ["empty query", { query: "", chosen_entity_id: null }, "query"],
    ["query too long", { query: "x".repeat(LIMITS.query + 1), chosen_entity_id: null }, "query"],
    ["entity id with spaces", { query: "x", chosen_entity_id: "a b" }, "chosen_entity_id"],
  ])("rejects %s", (_label, value, field) => {
    expect(firstError(confirmSchema, value)).toBe(field);
  });
});

describe("constraintsSchema", () => {
  it("accepts valid constraints", () => {
    expect(firstError(constraintsSchema, constraints)).toBeNull();
  });

  it.each([
    ["unknown size tier", { size_tier: "huge" }, "size_tier"],
    ["negative months", { exclude_recent_months: -1 }, "exclude_recent_months"],
    ["too many months", { exclude_recent_months: LIMITS.recentMonths + 1 }, "exclude_recent_months"],
    ["zero candidates", { max_candidates: 0 }, "max_candidates"],
    ["too many candidates", { max_candidates: LIMITS.maxCandidates + 1 }, "max_candidates"],
    ["too many tags", { include_tags: Array(LIMITS.tags + 1).fill("t") }, "include_tags"],
    ["tag too long", { exclude_tags: ["t".repeat(LIMITS.tag + 1)] }, "exclude_tags.0"],
  ])("rejects %s", (_label, patch, field) => {
    expect(firstError(constraintsSchema, { ...constraints, ...patch })).toBe(field);
  });
});

describe("runSchema", () => {
  it("defaults the message to empty", () => {
    expect(runSchema.parse({ venue_id: "ven_1", constraints }).message).toBe("");
  });

  it.each([
    [
      "message too long",
      { venue_id: "ven_1", constraints, message: "x".repeat(LIMITS.message + 1) },
      "message",
    ],
    ["missing venue", { constraints }, "venue_id"],
    ["bad constraints", { venue_id: "ven_1", constraints: {} }, "constraints.include_tags"],
  ])("rejects %s", (_label, value, field) => {
    expect(firstError(runSchema, value)).toBe(field);
  });
});

describe("idSchema", () => {
  it.each(["ven_abc123", "run_X-1", "urn:entity:artist:1.2"])("accepts %s", (value) => {
    expect(idSchema.safeParse(value).success).toBe(true);
  });

  it.each(["", "../x", "a/b", "a b", "x".repeat(LIMITS.id + 1)])("rejects %j", (value) => {
    expect(idSchema.safeParse(value).success).toBe(false);
  });
});
