import { venueUpsertSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, proxy } from "@/src/server/proxy";

/** Create a venue, or update one by `id` (the agent drops its stale matches and profile). */
export async function POST(req: Request): Promise<Response> {
  return proxy(async () => agent.upsertVenue(await body(req, venueUpsertSchema)), 201);
}
