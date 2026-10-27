// A seed's sample as GET /api/seeds/{slug} serves it, built from the synthetic run fixture.
import type { SeedSample } from "@/src/lib/api-client";
import type { SeedSlug } from "@/src/lib/seeds";
import type { RunOut } from "@/types/agent";
import run from "./run.json";

export function seedSample(slug: SeedSlug): SeedSample {
  const runId = `run_seed_${slug}`;
  return {
    slug,
    title: "Synthetic demo room",
    venueId: `ven_seed_${slug}`,
    venueName: `Synthetic Seed ${slug}`,
    run: { ...(run as RunOut), run_id: runId, is_seed: true, seed_slug: slug },
    precomputed: { runId, soundcheck: {}, briefs: {}, compare: null },
    crossDomain: { places: [], films: [], brands: [] },
    played: {},
  };
}
