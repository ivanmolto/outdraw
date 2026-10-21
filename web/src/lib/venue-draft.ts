// The /venue/new form's draft state and its validation. Inputs hold strings (what the user
// typed); `parseDraft` turns them into a VenueUpsert with the T17 zod schema and returns
// friendly messages keyed by dotted field path ("name", "past_shows.2.date").
import type { z } from "zod";

import { LIMITS, venueUpsertSchema, type VenueUpsert } from "./schemas";

export interface ShowDraft {
  /** Client-only React key; never sent. */
  key: string;
  act: string;
  date: string;
  tickets_sold: string;
  capacity: string;
  ticket_price: string;
  gross: string;
}

export interface VenueDraft {
  name: string;
  city: string;
  country: string;
  capacity: string;
  past_shows: ShowDraft[];
}

export type FieldErrors = Record<string, string>;

export const SHOW_NUMBERS = ["tickets_sold", "capacity", "ticket_price", "gross"] as const;

let nextKey = 0;

export function emptyShow(): ShowDraft {
  nextKey += 1;
  return {
    key: `show-${nextKey}`,
    act: "",
    date: "",
    tickets_sold: "",
    capacity: "",
    ticket_price: "",
    gross: "",
  };
}

export function emptyDraft(): VenueDraft {
  return { name: "", city: "", country: "", capacity: "", past_shows: [] };
}

/** "" → undefined (optional field left blank); anything else → a number, NaN if not one. */
function num(value: string): number | undefined {
  const v = value.trim();
  return v === "" ? undefined : Number(v);
}

/** The JSON the API expects, before validation. Blank optional numbers are dropped. */
export function toInput(draft: VenueDraft, id?: string): unknown {
  return {
    ...(id && { id }),
    name: draft.name,
    city: draft.city,
    country: draft.country,
    capacity: num(draft.capacity) ?? Number.NaN,
    past_shows: draft.past_shows.map((s) => {
      const show: Record<string, unknown> = { act: s.act, date: s.date };
      for (const field of SHOW_NUMBERS) {
        const n = num(s[field]);
        if (n !== undefined) show[field] = n;
      }
      return show;
    }),
  };
}

const LABELS: Record<string, string> = {
  name: "Venue name",
  city: "City",
  country: "Country",
  capacity: "Capacity",
  act: "Act",
  date: "Date",
  tickets_sold: "Tickets sold",
  ticket_price: "Ticket price",
  gross: "Gross",
};

/** The label of the field at `path`, for messages and the error summary. */
export function fieldLabel(path: string): string {
  const parts = path.split(".");
  const field = parts.at(-1) ?? path;
  const label = LABELS[field] ?? field;
  if (parts[0] === "past_shows" && parts.length === 3) {
    return `Show ${Number(parts[1]) + 1}: ${label.toLowerCase()}`;
  }
  return label;
}

function message(issue: z.core.$ZodIssue): string {
  switch (issue.code) {
    case "too_small":
      if (issue.origin === "string") return "Required";
      return `Must be at least ${issue.minimum.toLocaleString("en-US")}`;
    case "too_big":
      if (issue.origin === "string") return `Keep it under ${issue.maximum} characters`;
      if (issue.origin === "array") return `At most ${issue.maximum} shows`;
      return `Must be at most ${issue.maximum.toLocaleString("en-US")}`;
    case "invalid_type":
      return issue.expected === "int" ? "Enter a whole number" : "Enter a number";
    case "invalid_format":
      return "Pick a date";
    default:
      return "Check this field";
  }
}

export type ParseResult =
  | { ok: true; data: VenueUpsert }
  | { ok: false; errors: FieldErrors };

export function parseDraft(draft: VenueDraft, id?: string): ParseResult {
  const parsed = venueUpsertSchema.safeParse(toInput(draft, id));
  if (parsed.success) return { ok: true, data: parsed.data };
  const errors: FieldErrors = {};
  for (const issue of parsed.error.issues) {
    const path = issue.path.join(".");
    errors[path] ??= message(issue);
  }
  return { ok: false, errors };
}

export const MAX_SHOWS = LIMITS.pastShows;
