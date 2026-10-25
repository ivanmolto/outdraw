// Smoke check of the deployed agent's seed data, as /v/[slug] will read it: every seed is
// ready, and each scored act's evidence trail passes the web's `candidateTrailSchema` (a
// trail that does not would make the whole seed page a 502). Reads only, nothing spent.
//
//   set -a && source .env.local && set +a && npm run check:seeds
//
// Uses GET /seeds/{slug}/page when the deployed agent has it; otherwise falls back to the
// per-route reads the page used before (`/seeds`, `/runs/{id}`, `/evidence`, `/calls`) and,
// when even `/seeds` fails, to the slugs the web knows (src/lib/seeds.ts).
import { SEED_SLUGS } from "../src/lib/seeds";
import { candidateTrailSchema } from "../src/lib/soundcheck";

const url = process.env.AGENT_URL?.replace(/\/+$/, "");
const key = process.env.INTERNAL_API_KEY;
if (!url || !key) {
  console.error("AGENT_URL and INTERNAL_API_KEY must be set (source .env.local).");
  process.exit(2);
}

interface Entry {
  entity_id: string;
  name: string;
  sub_scores?: unknown;
}
interface Run {
  run_id: string;
  venue_id: string;
  status: string;
  is_seed: boolean;
  result: { shortlist?: Entry[] } | null;
}
interface Evidence {
  entity_id: string;
  evidence: unknown;
}
interface Seed {
  slug: string;
  run_id: string;
  venue_id: string;
  ready: boolean;
  run_status: string | null;
  profile_stored: boolean;
  compare_ready: boolean;
  brief_entity_ids: string[];
}
interface Page {
  seed: Seed;
  run: Run;
  candidates: Evidence[];
  calls: unknown[];
  briefs: unknown[];
  compare: unknown | null;
}

async function get<T>(path: string): Promise<{ status: number; body: T | null }> {
  const res = await fetch(url + path, {
    headers: { "X-Draw-Internal-Key": key!, accept: "application/json" },
    signal: AbortSignal.timeout(60_000),
  });
  const body = (await res.json().catch(() => null)) as T | null;
  return { status: res.status, body };
}

let failures = 0;
const fail = (msg: string) => {
  failures += 1;
  console.log(`  FAIL ${msg}`);
};

function checkTrails(slug: string, run: Run, candidates: Evidence[]) {
  const scored = (run.result?.shortlist ?? []).filter((e) => e.sub_scores);
  const byId = new Map(candidates.map((c) => [c.entity_id, c]));
  let ok = 0;
  for (const act of scored) {
    const c = byId.get(act.entity_id);
    if (!c) {
      fail(`${slug}: no evidence row for ${act.name}`);
      continue;
    }
    const parsed = candidateTrailSchema.safeParse(c.evidence);
    if (parsed.success) ok += 1;
    else {
      const issue = parsed.error.issues[0];
      fail(`${slug}: trail of ${act.name} fails the schema at ${issue.path.join(".")}: ${issue.message}`);
    }
  }
  console.log(`  trails: ${ok}/${scored.length} scored acts pass candidateTrailSchema`);
}

async function viaPage(slug: string): Promise<boolean> {
  const { status, body } = await get<Page>(`/seeds/${encodeURIComponent(slug)}/page`);
  // FastAPI's own 404 for a route it does not have, as opposed to the route's "Unknown seed".
  if (status === 404 && (body as { detail?: unknown } | null)?.detail === "Not Found") return false;
  if (status !== 200 || !body) {
    fail(`${slug}: GET /seeds/${slug}/page → ${status}`);
    return true;
  }
  const { seed } = body;
  console.log(
    `  page: ready=${seed.ready} compare=${seed.compare_ready} briefs=${seed.brief_entity_ids.length} calls=${body.calls.length} candidates=${body.candidates.length}`,
  );
  if (body.briefs.length !== seed.brief_entity_ids.length) fail(`${slug}: briefs listed vs served differ`);
  if (seed.compare_ready && !body.compare) fail(`${slug}: compare listed but not served`);
  checkTrails(slug, body.run, body.candidates);
  return true;
}

async function viaRoutes(slug: string, seed: Seed | null) {
  const runId = seed?.run_id ?? `run_seed_${slug}`;
  const run = await get<Run>(`/runs/${encodeURIComponent(runId)}`);
  if (run.status !== 200 || !run.body) {
    fail(`${slug}: GET /runs/${runId} → ${run.status}`);
    return;
  }
  if (!run.body.is_seed || run.body.status !== "ok") {
    fail(`${slug}: run is_seed=${run.body.is_seed} status=${run.body.status}`);
  }
  const scored = (run.body.result?.shortlist ?? []).filter((e) => e.sub_scores);
  const candidates: Evidence[] = [];
  for (const act of scored) {
    const ev = await get<Evidence>(
      `/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(act.entity_id)}/evidence`,
    );
    if (ev.status === 200 && ev.body) candidates.push(ev.body);
    else fail(`${slug}: evidence for ${act.name} → ${ev.status}`);
  }
  const calls = await get<unknown[]>(`/runs/${encodeURIComponent(runId)}/calls`);
  const compare = await get<unknown>(`/runs/${encodeURIComponent(runId)}/compare`);
  console.log(
    `  routes: shortlist=${run.body.result?.shortlist?.length ?? 0} scored=${scored.length} calls=${calls.body?.length ?? "?"} compare=${compare.status === 200 ? "ready" : compare.status}`,
  );
  if (seed) {
    let briefs = 0;
    for (const id of seed.brief_entity_ids) {
      const b = await get<unknown>(
        `/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(id)}/brief`,
      );
      if (b.status === 200) briefs += 1;
      else fail(`${slug}: brief for ${id} → ${b.status}`);
    }
    console.log(`  briefs: ${briefs}/${seed.brief_entity_ids.length} served`);
  } else {
    // Nothing lists the briefs; probe the top five. A seed run refuses an uncached brief
    // with 409 instead of drafting one, so this spends nothing either way.
    let briefs = 0;
    for (const act of scored.slice(0, 5)) {
      const b = await get<unknown>(
        `/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(act.entity_id)}/brief`,
      );
      if (b.status === 200) briefs += 1;
      else if (b.status !== 409) fail(`${slug}: brief for ${act.name} → ${b.status}`);
    }
    console.log(`  briefs: ${briefs}/5 of the top acts precomputed (GET /seeds failed, so probed)`);
  }
  checkTrails(slug, run.body, candidates);
}

const health = await fetch(`${url}/health`, { signal: AbortSignal.timeout(60_000) });
console.log(`${url}/health → ${health.status}`);

const seeds = await get<{ seeds: Seed[] }>("/seeds");
let list: Seed[] | null = null;
if (seeds.status === 200 && seeds.body) {
  list = seeds.body.seeds;
  for (const s of list) {
    console.log(
      `${s.slug}: ready=${s.ready} run_status=${s.run_status} profile=${s.profile_stored} compare=${s.compare_ready} briefs=${s.brief_entity_ids.length}`,
    );
    if (!s.ready || !s.profile_stored) fail(`${s.slug}: not ready`);
  }
} else {
  fail(`GET /seeds → ${seeds.status} (the seed pages cannot list themselves)`);
  console.log(`falling back to the web's slug list: ${SEED_SLUGS.join(", ")}`);
}

for (const slug of list ? list.map((s) => s.slug) : SEED_SLUGS) {
  console.log(`\n== ${slug}`);
  if (!(await viaPage(slug))) {
    console.log("  (GET /seeds/{slug}/page not deployed; reading route by route)");
    await viaRoutes(slug, list?.find((s) => s.slug === slug) ?? null);
  }
}

console.log(failures ? `\n${failures} problem(s).` : "\nAll seeds ready; every trail parses.");
process.exit(failures ? 1 : 0);
