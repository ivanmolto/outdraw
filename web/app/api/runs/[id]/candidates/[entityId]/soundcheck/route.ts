import type { SoundcheckData } from "@/src/lib/soundcheck";
import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";
import { soundcheckData } from "@/src/server/soundcheck";

/** One candidate's evidence trail for the Soundcheck panel, joined with the summaries of
 * its less-than-ok calls and the act's pencil hold (src/server/soundcheck.ts). */
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
    return soundcheckData(evidence, calls);
  });
}
