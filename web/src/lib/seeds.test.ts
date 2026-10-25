// The web's copy of the seed slugs matches the agent's seeds file, in order.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { SEED_SLUGS, isSeedSlug } from "./seeds";

const SEEDS_FILE = fileURLToPath(new URL("../../../agent/seeds/venues.json", import.meta.url));

describe("SEED_SLUGS", () => {
  it("is the agent's seeds file, slug for slug", () => {
    const seeds = JSON.parse(readFileSync(SEEDS_FILE, "utf8")) as { slug: string }[];
    expect([...SEED_SLUGS]).toEqual(seeds.map((s) => s.slug));
  });

  it("recognises a seed slug and nothing else", () => {
    expect(isSeedSlug("nola-jazz")).toBe(true);
    expect(isSeedSlug("nola-jazz/")).toBe(false);
    expect(isSeedSlug("NOLA-JAZZ")).toBe(false);
    expect(isSeedSlug("")).toBe(false);
  });
});
