import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

export async function GET(_req: Request, ctx: RouteContext<"/api/runs/[id]">): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.getRun(id(params.id, "id")));
}
