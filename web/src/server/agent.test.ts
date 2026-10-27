import { describe, expect, it } from "vitest";

import { AgentError, agentError } from "./agent";

describe("agentError", () => {
  it("is the instance itself, details and all", () => {
    const err = new AgentError(409, "conflict", ["Synthetic Act"]);

    expect(agentError(err)).toBe(err);
    expect(err.digest).toBe("DRAW_AGENT;409;conflict");
  });

  it("rebuilds one from the digest a `use cache` boundary leaves of it", () => {
    const stripped = Object.assign(new Error("An error occurred in the Server Components render."), {
      digest: new AgentError(503, "unavailable").digest,
    });

    expect(agentError(stripped)).toMatchObject({ status: 503, reason: "unavailable" });
    expect(agentError(stripped)).toBeInstanceOf(AgentError);
  });

  it("refuses anything else, Next's own digests included", () => {
    expect(agentError(new Error("agent 502: unknown"))).toBeNull();
    expect(agentError(Object.assign(new Error("x"), { digest: "NEXT_HTTP_ERROR_FALLBACK;404" }))).toBeNull();
    expect(agentError(Object.assign(new Error("x"), { digest: "DRAW_AGENT;502;traceback" }))).toBeNull();
    expect(agentError(Object.assign(new Error("x"), { digest: "DRAW_AGENT;5xx;unknown" }))).toBeNull();
    expect(agentError({ digest: 12 })).toBeNull();
    expect(agentError(null)).toBeNull();
  });
});
