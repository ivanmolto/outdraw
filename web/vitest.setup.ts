// `connection()` throws outside a Next request scope, and vitest has none: the route and
// page tests call src/server/agent.ts directly, so let it resolve as it does at request time.
import { vi } from "vitest";

vi.mock("next/server", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/server")>()),
  connection: async () => {},
}));
