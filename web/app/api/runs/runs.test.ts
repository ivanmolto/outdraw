import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_URL, KEY, ctx, fakeAgent, jsonRequest } from "@/src/server/test-agent";
import { GET as getCalls } from "./[id]/calls/route";
import { GET as getEvidence } from "./[id]/candidates/[entityId]/evidence/route";
import { GET as getRun } from "./[id]/route";
import { POST as createRun } from "./route";

const constraints = {
  include_tags: [],
  exclude_tags: [],
  size_tier: "mid",
  exclude_recent_months: 6,
  max_candidates: 10,
};
const run = { run_id: "run_1", venue_id: "ven_1", status: "ok", constraints, result: {} };

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("POST /api/runs", () => {
  it("forwards the run with the default message", async () => {
    const sent = fakeAgent(Response.json(run));

    const res = await createRun(jsonRequest({ venue_id: "ven_1", constraints }));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(run);
    expect(sent).toEqual([
      {
        method: "POST",
        url: `${AGENT_URL}/runs`,
        key: KEY,
        body: { venue_id: "ven_1", constraints, message: "" },
      },
    ]);
  });

  it("rejects out-of-bounds constraints without calling the agent", async () => {
    const sent = fakeAgent();

    const res = await createRun(
      jsonRequest({ venue_id: "ven_1", constraints: { ...constraints, max_candidates: 500 } }),
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      reason: "invalid_input",
      field: "constraints.max_candidates",
    });
    expect(sent).toEqual([]);
  });

  it.each([
    [409, { detail: "No crowd profile for this venue." }, 409, { reason: "conflict" }],
    [429, { detail: "LLM cap", what: "llm" }, 429, { reason: "budget" }],
    [503, { reason: "live_mode_off", fallback_seed: "x" }, 503, { reason: "live_mode_off" }],
    [503, { detail: "DATABASE_URL is not set." }, 503, { reason: "unavailable" }],
    [401, { detail: "Missing or wrong X-Draw-Internal-Key header." }, 502, { reason: "unknown" }],
    [500, "Internal Server Error", 502, { reason: "unknown" }],
  ])("maps backend %i to %j", async (status, body, outStatus, out) => {
    fakeAgent(
      typeof body === "string"
        ? new Response(body, { status })
        : Response.json(body, { status }),
    );

    const res = await createRun(jsonRequest({ venue_id: "ven_1", constraints }));

    expect(res.status).toBe(outStatus);
    expect(await res.json()).toEqual(out);
  });

  it("maps a network failure or timeout to unavailable", async () => {
    fakeAgent(new DOMException("The operation timed out.", "TimeoutError"));

    const res = await createRun(jsonRequest({ venue_id: "ven_1", constraints }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ reason: "unavailable" });
  });
});

describe("GET /api/runs/[id]", () => {
  it("forwards the id", async () => {
    const sent = fakeAgent(Response.json(run));

    const res = await getRun(jsonRequest({}), ctx({ id: "run_1" }));

    expect(await res.json()).toEqual(run);
    expect(sent[0]).toMatchObject({ method: "GET", url: `${AGENT_URL}/runs/run_1`, key: KEY });
  });

  it("maps 404 to not_found", async () => {
    fakeAgent(Response.json({ detail: "Unknown run 'run_9'." }, { status: 404 }));

    const res = await getRun(jsonRequest({}), ctx({ id: "run_9" }));

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ reason: "not_found" });
  });
});

describe("GET /api/runs/[id]/candidates/[entityId]/evidence", () => {
  it("URL-encodes the entity id", async () => {
    const sent = fakeAgent(Response.json({ run_id: "run_1" }));

    await getEvidence(jsonRequest({}), ctx({ id: "run_1", entityId: "urn:entity:synthetic:2" }));

    expect(sent[0].url).toBe(
      `${AGENT_URL}/runs/run_1/candidates/urn%3Aentity%3Asynthetic%3A2/evidence`,
    );
  });

  it("rejects a malformed entity id", async () => {
    const sent = fakeAgent();

    const res = await getEvidence(jsonRequest({}), ctx({ id: "run_1", entityId: "a/b" }));

    expect(await res.json()).toEqual({ reason: "invalid_input", field: "entityId" });
    expect(sent).toEqual([]);
  });
});

describe("GET /api/runs/[id]/calls", () => {
  it("forwards to the agent", async () => {
    const sent = fakeAgent(Response.json([]));

    const res = await getCalls(jsonRequest({}), ctx({ id: "run_1" }));

    expect(await res.json()).toEqual([]);
    expect(sent[0]).toMatchObject({ method: "GET", url: `${AGENT_URL}/runs/run_1/calls` });
  });
});
