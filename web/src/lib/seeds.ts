// The demo venues' slugs (T29, `/agent/seeds/venues.json`), as the web knows them without
// reading the agent: the proxy answers a real 404 for any other `/v/{slug}` before the page
// streams (a streamed `notFound()` is a 200). Kept by hand because the proxy bundles for the
// edge and cannot import across the monorepo; `seeds.test.ts` fails when the two drift.
export const SEED_SLUGS = ["nola-jazz", "brooklyn-indie", "london-comedy"] as const;

export type SeedSlug = (typeof SEED_SLUGS)[number];

export const isSeedSlug = (slug: string): slug is SeedSlug =>
  (SEED_SLUGS as readonly string[]).includes(slug);
