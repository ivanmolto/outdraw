import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

/** The run's Qloo calls in order, as the agent recorded them (key already redacted). */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/runs/[id]/calls">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.getCalls(id(params.id, "id")));
}
