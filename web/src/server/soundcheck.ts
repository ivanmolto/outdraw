// One candidate's Soundcheck data from the agent's evidence row and the run's recorded calls,
// shared by the soundcheck route and the seed pages' precompute read (src/server/seeds.ts).
import "server-only";

import { SUMMARISED, candidateTrailSchema, type SoundcheckData } from "@/src/lib/soundcheck";
import type { QlooStatus } from "@/types/schemas";
import { AgentError, type CandidateEvidence, type QlooCall } from "./agent";

/** The trail with the envelope summary of each trail call that came back less than ok and
 * the act's pencil hold. Joined here so the browser gets the summaries only, never the
 * recorded envelopes' Qloo results. The agent's contract types the trail as a bare object,
 * so it is parsed here: a reshaped trail is a logged 502, not a half-rendered panel. */
export function soundcheckData(evidence: CandidateEvidence, calls: QlooCall[]): SoundcheckData {
  const parsed = candidateTrailSchema.safeParse(evidence.evidence);
  if (!parsed.success) {
    console.error(
      `evidence trail for ${evidence.run_id}/${evidence.entity_id} does not match the schema:`,
      parsed.error.issues[0],
    );
    throw new AgentError(502, "unknown");
  }
  const trail = parsed.data;
  const wanted = new Set(
    trail.qloo_calls.filter((c) => SUMMARISED.has(c.status)).map((c) => c.call_id),
  );
  const summaries: Record<string, string> = {};
  for (const call of calls) {
    if (!wanted.has(call.call_id) || !SUMMARISED.has(call.status as QlooStatus)) continue;
    const { summary, error } = call.envelope as {
      summary?: unknown;
      error?: { recovery?: unknown };
    };
    const text = typeof summary === "string" ? summary : error?.recovery;
    if (typeof text === "string" && text) summaries[call.call_id] = text;
  }
  return { trail, summaries, pencilled: evidence.pencilled ?? false };
}
