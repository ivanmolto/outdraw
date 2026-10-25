import { SUMMARISED, candidateTrailSchema, type SoundcheckData } from "@/src/lib/soundcheck";
import { AgentError, agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";
import type { QlooStatus } from "@/types/schemas";

/** One candidate's evidence trail for the Soundcheck panel, with the envelope summary of
 * each trail call that came back less than ok and the act's pencil hold. Joined here so the browser gets the
 * summaries only, never the recorded envelopes' Qloo results. The agent's contract types
 * the trail as a bare object, so it is parsed here: a reshaped trail is a logged 502, not
 * a half-rendered panel. */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/runs/[id]/candidates/[entityId]/soundcheck">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(async (): Promise<SoundcheckData> => {
    const runId = id(params.id, "id");
    const entityId = id(params.entityId, "entityId");
    const [evidence, calls] = await Promise.all([
      agent.getEvidence(runId, entityId),
      agent.getCalls(runId),
    ]);
    const parsed = candidateTrailSchema.safeParse(evidence.evidence);
    if (!parsed.success) {
      console.error(
        `evidence trail for ${runId}/${entityId} does not match the schema:`,
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
  });
}
