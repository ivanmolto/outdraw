// A stand-in for the agent service for the Playwright suite: the routes the venue flow calls,
// answered with the synthetic fixtures the component tests use (no Qloo data). The Next
// server reaches it through AGENT_URL exactly as it reaches Railway, so the /api proxy,
// server components and the internal key all run for real. `GET /__requests` lists what
// it received, for the spec to assert on.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_AGENT_PORT ?? 8787);
const KEY = process.env.INTERNAL_API_KEY ?? "e2e-internal-key";

const fixture = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
const venueFixture = fixture("../app/venue/[id]/fixtures/venue.json");
const runFixture = fixture("../app/venue/[id]/shortlist/fixtures/run.json");

const VENUE_ID = venueFixture.id;
/** How long a run "takes", so the skeleton is visible. */
const RUN_DELAY_MS = 800;

/** @type {{method: string, path: string, body: unknown}[]} */
const requests = [];
/** @type {Record<string, unknown> | null} */
let venue = null;
let profiled = false;
/** @type {Record<string, unknown> | null} */
let lastRun = null;

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
    lastRun = { ...runFixture, venue_id: body.venue_id, constraints: body.constraints };
    return [200, lastRun];
  }
  if (method === "GET" && lastRun && path === `/runs/${lastRun.run_id}`) return [200, lastRun];
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
