import { connection } from "next/server";

import { proxy } from "@/src/server/proxy";
import { currentMe } from "@/src/server/session";

/** The session's plan, what it unlocks and the runs left this hour. Makes the anonymous
 * session (and sets its cookie) on the first visit. Reads only: outside the rate limits.
 * Per request: `connection()` keeps the build from prerendering it before the cookie and
 * the IP are read. */
export async function GET(req: Request): Promise<Response> {
  await connection();
  return proxy(() => currentMe(req));
}
