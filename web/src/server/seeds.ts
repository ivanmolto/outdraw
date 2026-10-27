// The demo venues (T29/T30) as the landing page and /v/[slug] read them: precomputed rows
// only, one `GET /seeds/{slug}/page` per seed, kept in the shared remote cache (`use cache:
// remote`: every instance, not each lambda's memory) so a seed page opens instantly and
// browsing it spends no Qloo or model call. The agent serves every byte of that read free
// for a seed run. Entries carry a tag per seed and one for the list; `POST
// /api/seeds/revalidate` (called by scripts/precompute.py) drops them when a seed is rebuilt.
// Callers must be request-bound (params or `connection()`): `seedAgent` skips `connection()`
// so these can run inside a cache scope, and nothing here may run in the build's prerender.
import "server-only";

import { cacheLife, cacheTag } from "next/cache";

import type { Precomputed } from "@/src/lib/precomputed";
import type { RunOut, SeedOut, SeedPageOut, VenueOut } from "@/types/agent";
import { AgentError, seedAgent } from "./agent";
import { soundcheckData } from "./soundcheck";

/** Tags the revalidate route drops: all seeds, or one by slug. */
export const SEEDS_TAG = "seeds";
export const seedTag = (slug: string) => `seed:${slug}`;

/** A landing-page card: a ready seed and enough of its venue to say what it is. */
export interface SeedCard {
  slug: string;
  title: string;
  description: string;
  venueName: string;
  city: string;
  capacity: number;
}

const card = (seed: SeedOut): SeedCard => ({
  slug: seed.slug,
  title: seed.title,
  description: seed.description,
  venueName: seed.venue.name,
  city: seed.venue.city,
  capacity: seed.venue.capacity,
});

/** The seeds precompute has finished, in file order. */
export async function seedCards(): Promise<SeedCard[]> {
  "use cache: remote";
  cacheLife("hours");
  cacheTag(SEEDS_TAG);
  const { seeds } = await seedAgent.getSeeds();
  return seeds.filter((s) => s.ready && s.profile_stored).map(card);
}

export type SeedPage =
  | { state: "missing" }
  /** Listed in venues.json but not precomputed yet (or only partly). */
  | { state: "not_ready"; title: string }
  | {
      state: "ready";
      seed: SeedCard;
      venueId: string;
      profile: NonNullable<VenueOut["profile"]>;
      run: RunOut;
      precomputed: Precomputed;
    };

/** The page's data from the agent's one read. */
function assemble(page: SeedPageOut): SeedPage {
  if (!page.venue.profile || !page.run.is_seed) {
    return { state: "not_ready", title: page.seed.title };
  }
  const scored = new Set(
    (page.run.result?.shortlist ?? []).filter((e) => e.sub_scores).map((e) => e.entity_id),
  );
  return {
    state: "ready",
    seed: card(page.seed),
    venueId: page.venue.id,
    profile: page.venue.profile,
    run: page.run,
    precomputed: {
      runId: page.run.run_id,
      soundcheck: Object.fromEntries(
        page.candidates
          .filter((c) => scored.has(c.entity_id))
          .map((c) => [c.entity_id, soundcheckData(c, page.calls)]),
      ),
      briefs: Object.fromEntries(page.briefs.map((b) => [b.entity_id, b])),
      compare: page.compare ?? null,
    },
  };
}

/** Everything /v/[slug] shows, read once per cache lifetime. A failed read throws (and is
 * not cached): the caller gets it stripped to its digest, which names the status and
 * reason (`agentError` in ./agent.ts). A missing or unfinished seed is cached briefly, a
 * ready one for hours. */
export async function seedPage(slug: string): Promise<SeedPage> {
  "use cache: remote";
  cacheTag(SEEDS_TAG, seedTag(slug));
  try {
    const page = await seedAgent.getSeedPage(slug);
    const out = assemble(page);
    if (out.state === "ready") cacheLife("hours");
    else cacheLife("minutes");
    return out;
  } catch (err) {
    if (err instanceof AgentError && err.reason === "not_found") {
      cacheLife("minutes");
      return { state: "missing" };
    }
    if (err instanceof AgentError && err.reason === "conflict") {
      cacheLife("minutes");
      return { state: "not_ready", title: slug };
    }
    throw err;
  }
}
