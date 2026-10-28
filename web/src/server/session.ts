// The anonymous session (T46): an httpOnly cookie naming a row in the agent's `sessions`
// table, which holds the plan. No login: a browser without the cookie, or with one the
// agent no longer knows, gets a new free session. HACKATHON_MODE is read here, server-side.
import "server-only";

import { cookies } from "next/headers";

import {
  effectivePlan,
  hackathonMode,
  me,
  remainingOf,
  type Me,
  type Plan,
} from "@/src/lib/entitlements";
import { idSchema } from "@/src/lib/schemas";
import { agent, agentError, type SessionOut } from "./agent";
import {
  checkPlanLimit,
  LIMITS,
  PLAN_LIMITS,
  remainingPlanRuns,
  remainingRuns,
} from "./ratelimit";

export const SESSION_COOKIE = "outdraw_session";

/** A year: the session only carries a plan, so it can outlive a booking season. */
const MAX_AGE_S = 365 * 24 * 60 * 60;

export function isHackathon(env: NodeJS.ProcessEnv = process.env): boolean {
  return hackathonMode(env.HACKATHON_MODE);
}

/** The request's session, made (and its cookie set) when there is none or it is unknown. */
export async function currentSession(): Promise<SessionOut> {
  const jar = await cookies();
  const existing = jar.get(SESSION_COOKIE)?.value;
  if (existing && idSchema.safeParse(existing).success) {
    try {
      return await agent.getSession(existing);
    } catch (err) {
      if (agentError(err)?.reason !== "not_found") throw err;
    }
  }
  const created = await agent.createSession();
  jar.set(SESSION_COOKIE, created.id, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: MAX_AGE_S,
  });
  return created;
}

/** The session and the plan in force for it. */
async function sessionPlan(): Promise<{ id: string; plan: Plan }> {
  const session = await currentSession();
  return { id: session.id, plan: effectivePlan(session.plan, isHackathon()) };
}

/** `GET /api/me` for `req`: the plan in force, its features and the runs left (the tighter
 * of the IP's hour and the plan's day). */
export async function currentMe(req: Request): Promise<Me> {
  const [session, hourly] = await Promise.all([currentSession(), remainingRuns(req)]);
  const plan = effectivePlan(session.plan, isHackathon());
  const daily = await remainingPlanRuns(session.id, plan);
  return me(
    session.plan,
    isHackathon(),
    remainingOf(hourly, daily),
    LIMITS.hourly.tokens,
    PLAN_LIMITS[plan].tokens,
  );
}

/** The 429 `plan_limit` when the request's session has no run left today on its plan,
 * else null with a run taken: the run routes call this before the agent. A browser
 * without a session gets one here (and its cookie), on the free plan. */
export async function checkPlanRun(): Promise<Response | null> {
  const { id, plan } = await sessionPlan();
  return checkPlanLimit(id, plan);
}
