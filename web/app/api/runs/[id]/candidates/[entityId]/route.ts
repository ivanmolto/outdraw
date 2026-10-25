import { pencilSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, id, proxy } from "@/src/server/proxy";

/** Pencil (or release) a hold on the act: stored on the agent's candidate row, so the
 * Soundcheck panel reopens with it after a reload. */
export async function PATCH(
  req: Request,
  ctx: RouteContext<"/api/runs/[id]/candidates/[entityId]">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(async () => {
    const runId = id(params.id, "id");
    const entityId = id(params.entityId, "entityId");
    return agent.setPencil(runId, entityId, await body(req, pencilSchema));
  });
}
