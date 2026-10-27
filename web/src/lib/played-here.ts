// Which shortlisted acts have played the venue before, from the venue's own past shows
// (Outdraw data, not Qloo's): the last night and how much of the room it sold. A past show
// is tied to an act through its confirmed resolution, matching names the way the agent's
// `last_booked` does (agent/draw/ranking/candidates.py), so the chip and the novelty
// score agree on who played here.
import type { VenueOut } from "@/types/agent";

export interface PlayedHere {
  /** The latest show, YYYY-MM-DD. */
  date: string;
  /** Tickets sold over the room that night (the show's capacity, else the venue's), 0-1;
   * null when the show has no tickets or capacity recorded. */
  sellThrough: number | null;
}

/** By Qloo entity ID. */
export type PlayedHereMap = Record<string, PlayedHere>;

/** The agent's `normalise_name` (agent/draw/profile/resolve.py): lowercase, no accents or
 * punctuation, "&" as "and", no leading "the", no spaces. */
export function normaliseName(name: string): string {
  let text = name.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
  text = text.replaceAll("&", " and ");
  text = text.replace(/[^\p{L}\p{N}_\s]/gu, "").replace(/\s+/g, " ").trim();
  return text.replace(/^the\s+/, "").replaceAll(" ", "");
}

type Venue = Pick<VenueOut, "venue" | "resolutions">;
type Show = VenueOut["venue"]["past_shows"][number];

function sellThrough(show: Show, roomCapacity: number): number | null {
  const room = show.capacity ?? roomCapacity;
  if (show.tickets_sold == null || !room) return null;
  return Math.min(Math.max(show.tickets_sold / room, 0), 1);
}

export function playedHere({ venue, resolutions }: Venue): PlayedHereMap {
  const latest = new Map<string, Show>();
  for (const show of venue.past_shows) {
    const key = normaliseName(show.act);
    const seen = latest.get(key);
    if (!seen || show.date > seen.date) latest.set(key, show);
  }
  const out: PlayedHereMap = {};
  for (const item of resolutions) {
    if (item.kind !== "act" || !item.confirmed || !item.entity_id) continue;
    for (const name of [item.query, item.canonical_name]) {
      const show = latest.get(normaliseName(name));
      const prior = out[item.entity_id];
      if (show && (!prior || show.date > prior.date)) {
        out[item.entity_id] = { date: show.date, sellThrough: sellThrough(show, venue.capacity) };
      }
    }
  }
  return out;
}
