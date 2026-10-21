import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

export async function GET(_req: Request, ctx: RouteContext<"/api/venues/[id]">): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.getVenue(id(params.id, "id")));
}
