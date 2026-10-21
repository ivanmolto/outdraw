import { readFileSync } from "node:fs";

import { expect, it } from "vitest";

it("web/.env.example names no NEXT_PUBLIC_ variable", () => {
  const example = readFileSync(new URL("../../.env.example", import.meta.url), "utf8");
  const names = example.match(/^\s*[A-Z0-9_]+(?==)/gm) ?? [];

  expect(names).toContain("INTERNAL_API_KEY");
  // Not even in a comment, so `grep -r NEXT_PUBLIC_ web/.env.example` prints nothing.
  expect(example).not.toContain("NEXT_PUBLIC_");
});
