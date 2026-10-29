import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { peekRateLimit, spendRateLimit } from "@/src/server/ratelimit";
import { checkPlanRun } from "@/src/server/session";
import { AGENT_URL, KEY, ctx, fakeAgent, jsonRequest } from "@/src/server/test-agent";
import soundcheck from "../../venue/[id]/shortlist/fixtures/soundcheck.json";
import { GET as getBrief } from "./[id]/candidates/[entityId]/brief/route";
import { GET as getEvidence } from "./[id]/candidates/[entityId]/evidence/route";
import { GET as getSoundcheck } from "./[id]/candidates/[entityId]/soundcheck/route";
import { PATCH as patchCandidate } from "./[id]/candidates/[entityId]/route";
import { GET as getCompare } from "./[id]/compare/route";
import { POST as refineRun } from "./[id]/refine/route";
import { GET as getRun } from "./[id]/route";
import { POST as createRun } from "./route";

// The brief route limits itself (peek, then spend when the agent drafted); the limiter is
// tested on its own in src/server/ratelimit.test.ts.
vi.mock("@/src/server/ratelimit", () => ({
  peekRateLimit: vi.fn(async () => null),
  spendRateLimit: vi.fn(async () => {}),
}));
const peek = vi.mocked(peekRateLimit);
const spend = vi.mocked(spendRateLimit);

// The plan's daily allowance (T48) gates runs and refinements; it is tested with the
// session in src/server/session.test.ts and src/server/ratelimit.test.ts.
vi.mock("@/src/server/session", () => ({ checkPlanRun: vi.fn(async () => null) }));
const planRun = vi.mocked(checkPlanRun);

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
  peek.mockReset().mockResolvedValue(null);
  spend.mockReset().mockResolvedValue(undefined);
  planRun.mockReset().mockResolvedValue(null);
});

describe("POST /api/runs", () => {
  it("answers the plan limit's 429 as it is, without calling the agent", async () => {
    const sent = fakeAgent(Response.json(run));
    const refused = { reason: "plan_limit", plan: "free", runs_per_day: 3, fallback_seed: "nola-jazz" };
    planRun.mockResolvedValue(Response.json(refused, { status: 429 }));

    const res = await createRun(jsonRequest({ venue_id: "ven_1", constraints }));

    expect(res.status).toBe(429);
    expect(await res.json()).toEqual(refused);
    expect(sent).toEqual([]);

    const refine = await refineRun(jsonRequest({ message: "more emerging" }), ctx({ id: "run_1" }));
    expect(refine.status).toBe(429);
    expect(sent).toEqual([]);
  });

  it("checks the plan only after the body is valid, so a bad request spends no run", async () => {
    fakeAgent(Response.json(run));
    const res = await createRun(jsonRequest({ venue_id: "ven_1", constraints: { max_candidates: 999 } }));
    expect(res.status).toBe(400);
    expect(planRun).not.toHaveBeenCalled();
  });

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
    [
      503,
      { reason: "live_mode_off", fallback_seed: "brooklyn-indie" },
      503,
      { reason: "live_mode_off", fallback_seed: "brooklyn-indie" },
    ],
    [
      429,
      { reason: "budget", what: "Qloo", fallback_seed: "london-comedy" },
      429,
      { reason: "budget", fallback_seed: "london-comedy" },
    ],
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

describe("POST /api/runs/[id]/refine", () => {
  it("forwards the trimmed message to the run's refine route", async () => {
    const sent = fakeAgent(Response.json({ ...run, run_id: "run_2", parent_run_id: "run_1" }));

    const res = await refineRun(jsonRequest({ message: "  More emerging acts " }), ctx({ id: "run_1" }));

    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ run_id: "run_2", parent_run_id: "run_1" });
    expect(sent).toEqual([
      {
        method: "POST",
        url: `${AGENT_URL}/runs/run_1/refine`,
        key: KEY,
        body: { message: "More emerging acts" },
      },
    ]);
  });

  it.each([
    [{ message: "   " }, "message"],
    [{ message: "\x00\n\x07" }, "message"],
    [{ message: "x".repeat(301) }, "message"],
    [{ message: "ok", extra: 1 }, undefined],
  ])("rejects %j without calling the agent", async (input, field) => {
    const sent = fakeAgent();

    const res = await refineRun(jsonRequest(input), ctx({ id: "run_1" }));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ reason: "invalid_input", ...(field && { field }) });
    expect(sent).toEqual([]);
  });

  it.each([
    [404, { reason: "not_found" }],
    [422, { reason: "invalid_input" }],
    [429, { reason: "budget" }],
  ])("maps backend %i to %j", async (status, out) => {
    fakeAgent(Response.json({ detail: "x" }, { status }));

    const res = await refineRun(jsonRequest({ message: "hello" }), ctx({ id: "run_1" }));

    expect(await res.json()).toEqual(out);
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

describe("GET /api/runs/[id]/candidates/[entityId]/soundcheck", () => {
  const call = (call_id: string, status: string, envelope: Record<string, unknown>) => ({
    call_id,
    workflow: "compare_audiences",
    status,
    cache_hit: false,
    request: {},
    envelope,
    created_at: "2026-10-21T12:00:00Z",
  });
  // The fixture's trail with its calls renamed to short ids, one per status of interest.
  const trail = {
    ...soundcheck.trail,
    qloo_calls: [
      { call_id: "c-ok", workflow: "recommend", status: "ok", cache_hit: false, params_summary: "" },
      { call_id: "c-partial", workflow: "compare_audiences", status: "partial", cache_hit: true, params_summary: "" },
      { call_id: "c-error", workflow: "trends", status: "error", cache_hit: false, params_summary: "" },
    ],
    affinities: [],
    source_labels: Object.fromEntries(
      Object.entries(soundcheck.trail.source_labels).filter(
        ([k]) => !k.startsWith("call:") && !k.startsWith("affinity:"),
      ),
    ),
  };

  it("joins the trail with summaries of its non-ok calls, never the envelopes' results", async () => {
    const sent = fakeAgent(
      Response.json({
        run_id: "run_1",
        entity_id: "urn:entity:synthetic:2",
        score: {},
        evidence: trail,
        pencilled: true,
        pencil_date: "2026-11-14",
      }),
      Response.json([
        call("c-ok", "ok", { status: "ok", summary: "fine", results: [{ name: "Secret" }] }),
        call("c-partial", "partial", { status: "partial", summary: "Half came back.", results: [{ x: 1 }] }),
        call("c-error", "error", { status: "error", error: { recovery: "Retry later." } }),
        call("c-other", "degraded", { status: "degraded", summary: "Another act's call." }),
      ]),
    );

    const res = await getSoundcheck(
      jsonRequest({}),
      ctx({ id: "run_1", entityId: "urn:entity:synthetic:2" }),
    );

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({
      trail,
      summaries: { "c-partial": "Half came back.", "c-error": "Retry later." },
      pencilled: true,
      pencilDate: "2026-11-14",
    });
    expect(JSON.stringify(body)).not.toContain("Secret");
    expect(sent.map((s) => s.url)).toEqual([
      `${AGENT_URL}/runs/run_1/candidates/urn%3Aentity%3Asynthetic%3A2/evidence`,
      `${AGENT_URL}/runs/run_1/calls`,
    ]);
  });

  it("refuses a trail that no longer matches the schema, as unknown", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fakeAgent(
      Response.json({
        run_id: "run_1",
        entity_id: "x",
        score: {},
        // A sub-score renamed on the agent: the panel must not render a half trail.
        evidence: { ...trail, sub_scores: { ...trail.sub_scores, crowd_affinity: undefined } },
      }),
      Response.json([]),
    );

    const res = await getSoundcheck(jsonRequest({}), ctx({ id: "run_1", entityId: "x" }));

    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ reason: "unknown" });
  });

  it("maps a missing candidate to not_found", async () => {
    fakeAgent(Response.json({ detail: "nope" }, { status: 404 }), Response.json([]));

    const res = await getSoundcheck(jsonRequest({}), ctx({ id: "run_1", entityId: "x" }));

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ reason: "not_found" });
  });
});

describe("GET /api/runs/[id]/compare", () => {
  it("forwards to the agent", async () => {
    const out = { run_id: "run_1", label: "llm_only", acts: [], disclaimer: "LLM only", model: "m" };
    const sent = fakeAgent(Response.json(out));

    const res = await getCompare(jsonRequest({}), ctx({ id: "run_1" }));

    expect(await res.json()).toEqual(out);
    expect(sent[0]).toMatchObject({ method: "GET", url: `${AGENT_URL}/runs/run_1/compare` });
  });

  it("maps the model's daily cap to budget", async () => {
    fakeAgent(Response.json({ detail: "LLM cap", what: "llm" }, { status: 429 }));

    const res = await getCompare(jsonRequest({}), ctx({ id: "run_1" }));

    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ reason: "budget" });
  });
});

describe("the recorded Qloo calls", () => {
  it("have no browser-facing route: envelopes stay server-side (Qloo's terms)", () => {
    // The Soundcheck route reads them on the server and sends summaries only.
    const route = fileURLToPath(new URL("./[id]/calls/route.ts", import.meta.url));
    expect(existsSync(route)).toBe(false);
  });
});

describe("GET /api/runs/[id]/candidates/[entityId]/brief", () => {
  const act = ctx({ id: "run_1", entityId: "urn:entity:synthetic:2" });

  it("forwards to the agent with the entity id encoded; a cached brief costs no token", async () => {
    const brief = { run_id: "run_1", entity_id: "urn:entity:synthetic:2", cached: true };
    const sent = fakeAgent(Response.json(brief));
    const req = jsonRequest({});

    const res = await getBrief(req, act);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(brief);
    expect(sent).toEqual([
      {
        method: "GET",
        url: `${AGENT_URL}/runs/run_1/candidates/urn%3Aentity%3Asynthetic%3A2/brief`,
        key: KEY,
        body: undefined,
      },
    ]);
    expect(peek).toHaveBeenCalledWith(req);
    expect(spend).not.toHaveBeenCalled();
  });

  it("takes a token after the agent drafted the brief", async () => {
    fakeAgent(Response.json({ run_id: "run_1", entity_id: "x", cached: false }));
    const req = jsonRequest({});

    const res = await getBrief(req, act);

    expect(res.status).toBe(200);
    expect(peek).toHaveBeenCalledTimes(1);
    expect(spend).toHaveBeenCalledWith(req);
    // The gate came before the call, the charge after it.
    expect(peek.mock.invocationCallOrder[0]).toBeLessThan(spend.mock.invocationCallOrder[0]);
  });

  it("refuses a visitor with no token left before calling the agent", async () => {
    const sent = fakeAgent();
    const busy = Response.json({ reason: "busy", fallback_seed: "nola-jazz" }, { status: 429 });
    peek.mockResolvedValue(busy);

    const res = await getBrief(jsonRequest({}), act);

    expect(res).toBe(busy);
    expect(sent).toEqual([]);
    expect(spend).not.toHaveBeenCalled();
  });

  it("maps the model's daily cap to budget, and takes no token for it", async () => {
    fakeAgent(Response.json({ detail: "cap", what: "LLM token" }, { status: 429 }));

    const res = await getBrief(jsonRequest({}), ctx({ id: "run_1", entityId: "x" }));

    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ reason: "budget" });
    expect(spend).not.toHaveBeenCalled();
  });
});

describe("PATCH /api/runs/[id]/candidates/[entityId]", () => {
  const patch = (body: unknown) =>
    new Request("http://web.test/api", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: typeof body === "string" ? body : JSON.stringify(body),
    });

  it("forwards the pencil hold to the agent", async () => {
    const out = { run_id: "run_1", entity_id: "urn:entity:synthetic:2", pencilled: true };
    const sent = fakeAgent(Response.json(out));

    const res = await patchCandidate(
      patch({ pencilled: true }),
      ctx({ id: "run_1", entityId: "urn:entity:synthetic:2" }),
    );

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(out);
    expect(sent).toEqual([
      {
        method: "PATCH",
        url: `${AGENT_URL}/runs/run_1/candidates/urn%3Aentity%3Asynthetic%3A2`,
        key: KEY,
        body: { pencilled: true },
      },
    ]);
  });

  it("forwards the night held with the pencil", async () => {
    const out = { run_id: "run_1", entity_id: "x", pencilled: true, date: "2026-11-14" };
    const sent = fakeAgent(Response.json(out));

    const res = await patchCandidate(
      patch({ pencilled: true, date: "2026-11-14" }),
      ctx({ id: "run_1", entityId: "x" }),
    );

    expect(res.status).toBe(200);
    expect(sent[0].body).toEqual({ pencilled: true, date: "2026-11-14" });
  });

  it.each([
    [{}, "pencilled"],
    [{ pencilled: "yes" }, "pencilled"],
    [{ pencilled: true, date: "2026-02-30" }, "date"],
    [{ pencilled: true, extra: 1 }, undefined],
    ["not json", undefined],
  ])("rejects %j without calling the agent", async (body, field) => {
    const sent = fakeAgent();

    const res = await patchCandidate(patch(body), ctx({ id: "run_1", entityId: "x" }));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ reason: "invalid_input", ...(field && { field }) });
    expect(sent).toEqual([]);
  });

  it("maps an unknown candidate to not_found", async () => {
    fakeAgent(Response.json({ detail: "nope" }, { status: 404 }));

    const res = await patchCandidate(patch({ pencilled: false }), ctx({ id: "run_1", entityId: "x" }));

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ reason: "not_found" });
  });
});
