// `connection()` throws outside a Next request scope, and vitest has none: the route and
// page tests call src/server/agent.ts directly, so let it resolve as it does at request time.
import { vi } from "vitest";

vi.mock("next/server", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/server")>()),
  connection: async () => {},
}));

// `cacheLife` and `cacheTag` throw outside a `use cache` scope, and vitest compiles no
// directives: the cached seed reads (src/server/seeds.ts) run as plain functions.
vi.mock("next/cache", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/cache")>()),
  cacheLife: () => {},
  cacheTag: () => {},
}));
