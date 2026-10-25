import { revalidateTag } from "next/cache";
import { z } from "zod";

import { HEADER } from "@/src/server/agent";
import { SEEDS_TAG, seedTag } from "@/src/server/seeds";

/** The slug pattern of agent/draw/seeds.py; at most the seeds file holds. */
const bodySchema = z.object({
  slugs: z
    .array(z.string().regex(/^[a-z0-9]+(-[a-z0-9]+)*$/).max(40))
    .max(50)
    .default([]),
});

/** Drops the cached seed pages after `scripts/precompute.py` rebuilds them: every seed
 * when `slugs` is empty, else those seeds and the landing-page list. Guarded by the same
 * internal key the agent takes, so only our own tooling can call it. Outside proxy.ts's
 * matcher: it spends nothing and is never limited. */
export async function POST(req: Request): Promise<Response> {
  const key = process.env.INTERNAL_API_KEY;
  if (!key || req.headers.get(HEADER) !== key) {
    return Response.json({ reason: "unauthorized" }, { status: 401 });
  }
  const parsed = bodySchema.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success) return Response.json({ reason: "invalid_input" }, { status: 400 });

  const tags = parsed.data.slugs.length
    ? [SEEDS_TAG, ...parsed.data.slugs.map(seedTag)]
    : [SEEDS_TAG];
  for (const tag of tags) revalidateTag(tag, "max");
  return Response.json({ revalidated: tags });
}
