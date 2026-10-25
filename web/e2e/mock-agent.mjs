// A stand-in for the agent service for the Playwright suite: the routes the venue flow calls,
// answered with the synthetic fixtures the component tests use (no Qloo data). The Next
// server reaches it through AGENT_URL exactly as it reaches Render, so the /api proxy,
// server components and the internal key all run for real. `GET /__requests` lists what
// it received, for the spec to assert on.
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

const VENUE_ID = venueFixture.id;
/** How long a run "takes", so the skeleton is visible. */
const RUN_DELAY_MS = 800;

/** @type {{method: string, path: string, body: unknown}[]} */
const requests = [];
/** @type {Record<string, unknown> | null} */
let venue = null;
let profiled = false;
/** Stored runs by id, fresh and refined. @type {Map<string, any>} */
const runs = new Map();
/** Pencil holds by `${run_id} ${entity_id}`, as the real agent stores them on the row. */
const pencilled = new Map();
/** How long a brief "takes" to draft the first time, so the spinner is visible. */
const BRIEF_DELAY_MS = 600;
/** Briefs served once, so the second read is the cached copy. @type {Set<string>} */
const briefed = new Set();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  if (method === "POST" && path === "/venues") {
    venue = { ...body };
    delete venue.id;
    return [200, { id: VENUE_ID }];
  }
  if (!venue) return [404, { detail: "Unknown venue." }];
  if (method === "POST" && path === `/venues/${VENUE_ID}/resolve`) return [200, resolutions(venue)];
  if (method === "POST" && path === `/venues/${VENUE_ID}/profile`) {
    profiled = true;
    return [200, venueFixture.profile];
  }
  if (method === "GET" && path === `/venues/${VENUE_ID}`) {
    return [
      200,
      { id: VENUE_ID, venue, resolutions: [], profile: profiled ? venueFixture.profile : null },
    ];
  }
  if (method === "POST" && path === "/runs") {
    await sleep(RUN_DELAY_MS);
    const run = { ...runFixture, venue_id: body.venue_id, constraints: body.constraints };
    runs.set(run.run_id, run);
    return [200, run];
  }
  const refining = method === "POST" && /^\/runs\/([^/]+)\/refine$/.exec(path);
  if (refining) {
    const parent = runs.get(decodeURIComponent(refining[1]));
    if (!parent) return [404, { detail: "Unknown run." }];
    await sleep(RUN_DELAY_MS);
    const run = refine(parent, body.message);
    runs.set(run.run_id, run);
    return [200, run];
  }
  const reading = method === "GET" && /^\/runs\/([^/]+)$/.exec(path);
  if (reading && runs.has(decodeURIComponent(reading[1]))) {
    return [200, runs.get(decodeURIComponent(reading[1]))];
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
        },
      ];
    }
    if (method === "GET" && candidate[3] === "/brief") {
      const cached = briefed.has(key);
      if (!cached) await sleep(BRIEF_DELAY_MS);
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
      if (typeof body?.pencilled !== "boolean")
        return [422, { detail: "pencilled must be a boolean." }];
      pencilled.set(key, body.pencilled);
      return [200, { run_id: runId, entity_id: entityId, pencilled: body.pencilled }];
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
  if (req.headers["x-draw-internal-key"] !== KEY) return send(401, { detail: "Bad key." });

  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = raw ? JSON.parse(raw) : undefined;
  requests.push({ method: req.method, path: url.pathname, body });
  const [status, data] = await route(req.method, url.pathname, body);
  send(status, data);
}).listen(PORT, "127.0.0.1", () => {
  console.log(`mock agent on http://127.0.0.1:${PORT}`);
});
