import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/runs/[id]/candidates/[entityId]/evidence">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() =>
    agent.getEvidence(id(params.id, "id"), id(params.entityId, "entityId")),
  );
}
