import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_URL, KEY, ctx, fakeAgent, jsonRequest } from "@/src/server/test-agent";
import { POST as confirm } from "./[id]/confirm/route";
import { POST as profile } from "./[id]/profile/route";
import { POST as resolve } from "./[id]/resolve/route";
import { GET as getVenue } from "./[id]/route";
import { POST as upsert } from "./route";

const venue = {
  name: "Synthetic Room",
  city: "New York",
  country: "US",
  capacity: 300,
  past_shows: [{ act: "Synthetic Act", date: "2026-05-01" }],
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("POST /api/venues", () => {
  it("forwards the validated venue with the internal key", async () => {
    const sent = fakeAgent(Response.json({ id: "ven_1" }, { status: 201 }));

    const res = await upsert(jsonRequest({ ...venue, name: "  Synthetic Room " }));

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ id: "ven_1" });
    expect(sent).toEqual([
      { method: "POST", url: `${AGENT_URL}/venues`, key: KEY, body: venue },
    ]);
  });

  it("rejects invalid input without calling the agent", async () => {
    const sent = fakeAgent();
    const shows = Array.from({ length: 11 }, () => venue.past_shows[0]);

    const res = await upsert(jsonRequest({ ...venue, past_shows: shows }));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ reason: "invalid_input", field: "past_shows" });
    expect(sent).toEqual([]);
  });

  it.each([
    ["malformed JSON", "{not json"],
    ["an oversized body", JSON.stringify({ ...venue, name: "x".repeat(40_000) })],
  ])("rejects %s", async (_label, raw) => {
    const sent = fakeAgent();

    const res = await upsert(jsonRequest(raw));

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ reason: "invalid_input" });
    expect(sent).toEqual([]);
  });

  it("answers unavailable when the server env is missing", async () => {
    fakeAgent();
    vi.stubEnv("INTERNAL_API_KEY", "");

    const res = await upsert(jsonRequest(venue));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ reason: "unavailable" });
  });
});

describe("GET /api/venues/[id]", () => {
  it("forwards the id, URL-encoded", async () => {
    const out = { id: "ven_1", venue, resolutions: [], profile: null };
    const sent = fakeAgent(Response.json(out));

    const res = await getVenue(jsonRequest({}), ctx({ id: "ven_1" }));

    expect(await res.json()).toEqual(out);
    expect(sent[0]).toMatchObject({ method: "GET", url: `${AGENT_URL}/venues/ven_1`, key: KEY });
  });

  it("rejects a malformed id", async () => {
    const sent = fakeAgent();

    const res = await getVenue(jsonRequest({}), ctx({ id: "../runs" }));

    expect(await res.json()).toEqual({ reason: "invalid_input", field: "id" });
    expect(sent).toEqual([]);
  });

  it("maps 404 to not_found without leaking the detail", async () => {
    fakeAgent(Response.json({ detail: "Unknown venue 'ven_9'." }, { status: 404 }));

    const res = await getVenue(jsonRequest({}), ctx({ id: "ven_9" }));

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ reason: "not_found" });
  });
});

describe("POST /api/venues/[id]/resolve", () => {
  it("forwards to the agent", async () => {
    const sent = fakeAgent(Response.json({ venue_id: "ven_1", items: [] }));

    const res = await resolve(jsonRequest({}), ctx({ id: "ven_1" }));

    expect(res.status).toBe(200);
    expect(sent[0]).toMatchObject({ method: "POST", url: `${AGENT_URL}/venues/ven_1/resolve` });
  });

  it("maps a Qloo failure (502) to unavailable", async () => {
    fakeAgent(
      Response.json(
        { detail: "Qloo call failed.", error: { code: "QLOO_AUTH", retryable: false } },
        { status: 502 },
      ),
    );

    const res = await resolve(jsonRequest({}), ctx({ id: "ven_1" }));

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ reason: "unavailable" });
  });
});

describe("POST /api/venues/[id]/confirm", () => {
  it("forwards the pick", async () => {
    const pick = { query: "Synthetic Act", chosen_entity_id: "urn:entity:synthetic:1" };
    const sent = fakeAgent(Response.json({ ...pick, kind: "act", confirmed: true }));

    const res = await confirm(jsonRequest(pick), ctx({ id: "ven_1" }));

    expect(res.status).toBe(200);
    expect(sent[0]).toMatchObject({ url: `${AGENT_URL}/venues/ven_1/confirm`, body: pick });
  });

  it("maps the agent's 422 (not a candidate) to invalid_input", async () => {
    fakeAgent(Response.json({ detail: "not a candidate" }, { status: 422 }));

    const res = await confirm(
      jsonRequest({ query: "Synthetic Act", chosen_entity_id: "urn:entity:other:1" }),
      ctx({ id: "ven_1" }),
    );

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ reason: "invalid_input" });
  });
});

describe("POST /api/venues/[id]/profile", () => {
  it("maps 409 to conflict with the items still to confirm", async () => {
    fakeAgent(
      Response.json(
        { detail: { message: "Confirm every match.", pending: ["Synthetic Act"] } },
        { status: 409 },
      ),
    );

    const res = await profile(jsonRequest({}), ctx({ id: "ven_1" }));

    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ reason: "conflict", pending: ["Synthetic Act"] });
  });

  it("maps a budget stop (429) to budget", async () => {
    fakeAgent(Response.json({ detail: "cap", what: "qloo", used: 300, cap: 300 }, { status: 429 }));

    const res = await profile(jsonRequest({}), ctx({ id: "ven_1" }));

    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ reason: "budget" });
  });
});
