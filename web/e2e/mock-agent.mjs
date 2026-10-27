// A stand-in for the agent service for the Playwright suite: the routes the venue flow calls,
// answered with the synthetic fixtures the component tests use (no Qloo data). The Next
// server reaches it through AGENT_URL exactly as it reaches Render, so the /api proxy,
// server components and the internal key all run for real. `GET /__requests` lists what
// it received, for the spec to assert on, and `GET /__counters` how many Qloo and model calls
// the real agent would have spent on it (the daily counters of agent/draw/qloo/budget.py).
// `POST /__refuse {status}` makes every call that would spend (runs, refinements, resolve,
// profile, a first brief or compare) answer as the real agent refuses it (503 with
// LIVE_MODE=off, 429 at the daily budget, both naming a fallback seed) until
// `POST /__refuse {status: null}`; stored and cached reads still answer.
// `POST /__fail {mode}` breaks the whole agent for T40's error states: "crash" answers
// every call with FastAPI's bare 500 plus a traceback and its own URL in the body (what
// must never reach a browser), "down" drops every connection (the agent unreachable);
// `{mode: null}` restores it.
// Three seed venues (T29) are stored from the start, read-only as the agent keeps them.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_AGENT_PORT ?? 8787);
const KEY = process.env.INTERNAL_API_KEY ?? "e2e-internal-key";

const fixture = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const venueFixture = fixture("../app/venue/[id]/fixtures/venue.json");
const runFixture = fixture("../app/venue/[id]/shortlist/fixtures/run.json");
const refinedFixture = fixture("../app/venue/[id]/shortlist/fixtures/refined-run.json");
const soundcheckFixture = fixture("../app/venue/[id]/shortlist/fixtures/soundcheck.json");
const briefFixture = fixture("../app/venue/[id]/shortlist/fixtures/brief.json");
const compareFixture = fixture("../app/venue/[id]/shortlist/fixtures/compare.json");

const VENUE_ID = venueFixture.id;
/** How long a run "takes", so the skeleton is visible. */
const RUN_DELAY_MS = 800;

/** @type {{method: string, path: string, body: unknown}[]} */
const requests = [];
/** @type {Record<string, unknown> | null} */
let venue = null;
let profiled = false;
/** Stored runs by id: the seeds', fresh and refined. @type {Map<string, any>} */
const runs = new Map();
/** Pencil holds by `${run_id} ${entity_id}`, as the real agent stores them on the row. */
const pencilled = new Map();
/** The night each hold is for, by the same key (cleared on a release). */
const pencilDates = new Map();
/** How long a brief "takes" to draft the first time, so the spinner is visible. */
const BRIEF_DELAY_MS = 600;
/** Briefs served once, so the second read is the cached copy. @type {Set<string>} */
const briefed = new Set();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** What a call would have spent on the real agent's daily counters. */
const counters = { qloo: 0, llm: 0 };
const spend = (qloo, llm) => {
  counters.qloo += qloo;
  counters.llm += llm;
};
/** Runs compared once, so a second compare is the cached copy. @type {Set<string>} */
const compared = new Set();

/** The seed the refusals name: not the web's default, so a spec sees the agent's choice. */
const REFUSAL_SEED = "brooklyn-indie";
const REFUSALS = {
  503: {
    reason: "live_mode_off",
    detail: "Live analysis is switched off (LIVE_MODE=off).",
    fallback_seed: REFUSAL_SEED,
  },
  429: {
    reason: "budget",
    detail: "Qloo daily budget exceeded: 300/300",
    what: "Qloo",
    used: 300,
    cap: 300,
    fallback_seed: REFUSAL_SEED,
  },
};
/** The status runs and refinements are refused with, or null to run them. */
let refuseWith = null;

/** How the whole agent fails ("crash" | "down"), or null when it works. */
let failMode = null;
/** A crashed route's body: what an unhandled exception in FastAPI leaks when debug is on. */
const crashBody = (path) => ({
  detail: "Internal Server Error",
  traceback: [
    "Traceback (most recent call last):",
    '  File "/app/draw/api/routes.py", line 42, in get_venue',
    "    raise RuntimeError(\"synthetic crash\")",
    "RuntimeError: synthetic crash",
  ].join("\n"),
  url: `http://127.0.0.1:${PORT}${path}`,
});

// --- seeds ------------------------------------------------------------------------------

/** The seed venues: synthetic copies of the venue fixture, each with the run fixture as its
 * precomputed run, a brief for its headliner only and its compare. */
const SEEDS = [
  ["nola-jazz", "Synthetic jazz bar", 150],
  ["brooklyn-indie", "Synthetic indie room", 400],
  ["london-comedy", "Synthetic comedy club", 250],
].map(([slug, title, capacity], i) => {
  const venueId = `ven_seed_${slug}`;
  const runId = `run_seed_${slug}`;
  const input = { ...venueFixture.venue, name: `Synthetic Room ${i + 1}`, capacity };
  return {
    slug,
    title,
    description: `A ${capacity}-capacity synthetic room for the e2e suite.`,
    venue_id: venueId,
    run_id: runId,
    venue: input,
    constraints: runFixture.constraints,
    message: "",
    ready: true,
    venue_stored: true,
    profile_stored: true,
    run_status: "ok",
    compare_ready: true,
    brief_entity_ids: [runFixture.result.shortlist[0].entity_id],
    created_at: runFixture.created_at,
  };
});
const seedVenues = new Map(
  SEEDS.map((s) => [
    s.venue_id,
    {
      id: s.venue_id,
      venue: s.venue,
      resolutions: [],
      profile: { ...venueFixture.profile, venue_id: s.venue_id },
    },
  ]),
);
const seedByRun = new Map(SEEDS.map((s) => [s.run_id, s]));
for (const s of SEEDS) {
  runs.set(s.run_id, {
    ...runFixture,
    run_id: s.run_id,
    venue_id: s.venue_id,
    is_seed: true,
    seed_slug: s.slug,
  });
}

function resolutions(input) {
  return {
    venue_id: VENUE_ID,
    items: [
      {
        kind: "venue",
        query: `venue:${input.name}`,
        confirmed: true,
        entity_id: "urn:entity:synthetic:place:1",
        canonical_name: input.name,
        candidates: [
          { entity_id: "urn:entity:synthetic:place:1", name: input.name, type: "urn:entity:place" },
        ],
      },
      ...input.past_shows.map((show, i) => ({
        kind: "act",
        query: show.act,
        confirmed: true,
        entity_id: `urn:entity:synthetic:artist:${i + 1}`,
        canonical_name: show.act,
        candidates: [
          {
            entity_id: `urn:entity:synthetic:artist:${i + 1}`,
            name: show.act,
            type: "urn:entity:artist",
          },
        ],
      })),
    ],
  };
}

/** A refinement of `parent`: the first one is the refined fixture (an act enters, one
 * leaves, two move); later ones rotate the parent's list by one place, so every step shows
 * moves. Ids are numbered so a chain of any length stays distinct. */
function refine(parent, message) {
  const n = runs.size;
  const base = parent.parent_run_id ? parent : refinedFixture;
  const shortlist = parent.parent_run_id
    ? [...parent.result.shortlist.slice(1), parent.result.shortlist[0]]
    : refinedFixture.result.shortlist;
  return {
    ...base,
    run_id: `run_refined_${n}`,
    venue_id: parent.venue_id,
    parent_run_id: parent.run_id,
    result: {
      ...base.result,
      shortlist,
      refinement: {
        ...refinedFixture.result.refinement,
        message,
        explanation: `Applied "${message}" to the previous shortlist.`,
      },
    },
  };
}

async function route(method, path, body) {
  if (method === "GET" && path === "/seeds") return [200, { seeds: SEEDS }];
  const seedPage = method === "GET" && /^\/seeds\/([^/]+)\/page$/.exec(path);
  if (seedPage) {
    const seed = SEEDS.find((s) => s.slug === decodeURIComponent(seedPage[1]));
    if (!seed) return [404, { detail: "Unknown seed." }];
    const run = runs.get(seed.run_id);
    const candidates = run.result.shortlist
      .filter((e) => e.sub_scores)
      .map((e) => ({
        run_id: seed.run_id,
        entity_id: e.entity_id,
        score: {},
        evidence: { ...soundcheckFixture.trail, run_id: seed.run_id, entity_id: e.entity_id, name: e.name },
        pencilled: false,
      }));
    const briefs = seed.brief_entity_ids.map((entityId) => ({
      ...briefFixture,
      run_id: seed.run_id,
      entity_id: entityId,
      name: run.result.shortlist.find((e) => e.entity_id === entityId)?.name,
      cached: true,
    }));
    return [
      200,
      {
        seed,
        venue: seedVenues.get(seed.venue_id),
        run,
        candidates,
        calls: [],
        briefs,
        compare: seed.compare_ready ? { ...compareFixture, run_id: seed.run_id, cached: true } : null,
      },
    ];
  }
  const seedVenue = method === "GET" && /^\/venues\/([^/]+)$/.exec(path);
  if (seedVenue && seedVenues.has(decodeURIComponent(seedVenue[1]))) {
    return [200, seedVenues.get(decodeURIComponent(seedVenue[1]))];
  }
  if (method === "POST" && path === "/venues") {
    venue = { ...body };
    delete venue.id;
    return [200, { id: VENUE_ID }];
  }
  // The seeds are stored from the start; the live venue only once it is posted.
  if (!venue && path.startsWith("/venues/")) return [404, { detail: "Unknown venue." }];
  if (refuseWith && method === "POST" && /^\/venues\/[^/]+\/(resolve|profile)$/.test(path)) {
    return [refuseWith, REFUSALS[refuseWith]];
  }
  if (method === "POST" && path === `/venues/${VENUE_ID}/resolve`) {
    spend(1, 0);
    return [200, resolutions(venue)];
  }
  if (method === "POST" && path === `/venues/${VENUE_ID}/profile`) {
    spend(1, 0);
    profiled = true;
    return [200, venueFixture.profile];
  }
  if (method === "GET" && path === `/venues/${VENUE_ID}`) {
    return [
      200,
      { id: VENUE_ID, venue, resolutions: [], profile: profiled ? venueFixture.profile : null },
    ];
  }
  const isRun = method === "POST" && (path === "/runs" || /^\/runs\/[^/]+\/refine$/.test(path));
  // Refused before any work, as agent/draw/api/live.py does: nothing stored, nothing spent.
  if (isRun && refuseWith) return [refuseWith, REFUSALS[refuseWith]];
  if (method === "POST" && path === "/runs") {
    spend(1, 1);
    await sleep(RUN_DELAY_MS);
    const run = { ...runFixture, venue_id: body.venue_id, constraints: body.constraints };
    runs.set(run.run_id, run);
    return [200, run];
  }
  const refining = method === "POST" && /^\/runs\/([^/]+)\/refine$/.exec(path);
  if (refining) {
    const parent = runs.get(decodeURIComponent(refining[1]));
    if (!parent) return [404, { detail: "Unknown run." }];
    spend(1, 1);
    await sleep(RUN_DELAY_MS);
    const run = refine(parent, body.message);
    runs.set(run.run_id, run);
    return [200, run];
  }
  const comparing = method === "GET" && /^\/runs\/([^/]+)\/compare$/.exec(path);
  if (comparing && runs.has(decodeURIComponent(comparing[1]))) {
    const runId = decodeURIComponent(comparing[1]);
    const seed = seedByRun.get(runId);
    if (seed && !seed.compare_ready) return [409, { detail: { reason: "seed_not_precomputed" } }];
    const cached = !!seed || compared.has(runId);
    if (!cached && refuseWith) return [refuseWith, REFUSALS[refuseWith]];
    if (!cached) spend(0, 1);
    compared.add(runId);
    return [200, { ...compareFixture, run_id: runId, cached }];
  }
  const venueHolds = method === "GET" && /^\/venues\/([^/]+)\/holds$/.exec(path);
  if (venueHolds) {
    // Every pencilled act across the venue's stored runs, as the real route lists them.
    const venueId = decodeURIComponent(venueHolds[1]);
    const holds = [];
    for (const run of runs.values()) {
      if (run.venue_id !== venueId) continue;
      for (const e of run.result?.shortlist ?? []) {
        const key = `${run.run_id} ${e.entity_id}`;
        if (!pencilled.get(key)) continue;
        const date = pencilDates.get(key) ?? null;
        holds.push({ run_id: run.run_id, entity_id: e.entity_id, name: e.name, date });
      }
    }
    const night = (h) => h.date ?? "9999-12-31"; // undated last
    holds.sort((a, b) => night(a).localeCompare(night(b)));
    return [200, { venue_id: venueId, holds }];
  }
  const reading = method === "GET" && /^\/runs\/([^/]+)$/.exec(path);
  if (reading && runs.has(decodeURIComponent(reading[1]))) {
    // Each entry carries its hold, as the real agent serves a run read back.
    const run = runs.get(decodeURIComponent(reading[1]));
    const shortlist = (run.result?.shortlist ?? []).map((e) => {
      const key = `${run.run_id} ${e.entity_id}`;
      const hold = pencilled.get(key) ? { date: pencilDates.get(key) ?? null } : null;
      return { ...e, pencil_hold: hold };
    });
    return [200, run.result ? { ...run, result: { ...run.result, shortlist } } : run];
  }
  // The Soundcheck panel's routes, for any act of a stored run: every act gets the one
  // synthetic trail and brief, renamed to it; the pencil hold is kept per act.
  const candidate = /^\/runs\/([^/]+)\/candidates\/([^/]+)(\/evidence|\/brief)?$/.exec(path);
  if (candidate) {
    const runId = decodeURIComponent(candidate[1]);
    const entityId = decodeURIComponent(candidate[2]);
    const run = runs.get(runId);
    const act = run?.result.shortlist.find((e) => e.entity_id === entityId);
    if (!act) return [404, { detail: "No such candidate." }];
    const key = `${runId} ${entityId}`;
    if (method === "GET" && candidate[3] === "/evidence") {
      const trail = {
        ...soundcheckFixture.trail,
        run_id: runId,
        entity_id: entityId,
        name: act.name,
      };
      return [
        200,
        {
          run_id: runId,
          entity_id: entityId,
          score: {},
          evidence: trail,
          pencilled: pencilled.get(key) ?? false,
          pencil_date: pencilDates.get(key) ?? null,
        },
      ];
    }
    const seed = seedByRun.get(runId);
    if (method === "GET" && candidate[3] === "/brief") {
      if (seed && !seed.brief_entity_ids.includes(entityId)) {
        return [409, { detail: { reason: "seed_not_precomputed" } }];
      }
      const cached = !!seed || briefed.has(key);
      if (!cached && refuseWith) return [refuseWith, REFUSALS[refuseWith]];
      if (!cached) {
        spend(1, 1);
        await sleep(BRIEF_DELAY_MS);
      }
      briefed.add(key);
      return [
        200,
        {
          ...briefFixture,
          run_id: runId,
          entity_id: entityId,
          name: act.name,
          cached,
        },
      ];
    }
    if (method === "PATCH" && !candidate[3]) {
      if (seed) return [403, { detail: { reason: "seed_read_only" } }];
      if (typeof body?.pencilled !== "boolean")
        return [422, { detail: "pencilled must be a boolean." }];
      pencilled.set(key, body.pencilled);
      const date = body.pencilled ? (body.date ?? null) : null;
      pencilDates.set(key, date);
      return [200, { run_id: runId, entity_id: entityId, pencilled: body.pencilled, date }];
    }
  }
  const calls = method === "GET" && /^\/runs\/([^/]+)\/calls$/.exec(path);
  if (calls && runs.has(decodeURIComponent(calls[1]))) return [200, []];
  return [404, { detail: "Not found." }];
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://mock");
  const send = (status, data) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(data));
  };
  if (url.pathname === "/health") return send(200, { ok: true });
  if (url.pathname === "/__requests") return send(200, requests);
  if (url.pathname === "/__counters") return send(200, counters);

  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = raw ? JSON.parse(raw) : undefined;
  if (url.pathname === "/__refuse") {
    refuseWith = body?.status in REFUSALS ? body.status : null;
    return send(200, { status: refuseWith });
  }
  if (url.pathname === "/__fail") {
    failMode = body?.mode === "crash" || body?.mode === "down" ? body.mode : null;
    return send(200, { mode: failMode });
  }
  if (failMode === "down") return req.socket.destroy();
  if (req.headers["x-draw-internal-key"] !== KEY) return send(401, { detail: "Bad key." });

  // A crashed call is not recorded: other specs count the agent's real reads.
  if (failMode === "crash") return send(500, crashBody(url.pathname));
  requests.push({ method: req.method, path: url.pathname, body });
  const [status, data] = await route(req.method, url.pathname, body);
  send(status, data);
}).listen(PORT, "127.0.0.1", () => {
  console.log(`mock agent on http://127.0.0.1:${PORT}`);
});
