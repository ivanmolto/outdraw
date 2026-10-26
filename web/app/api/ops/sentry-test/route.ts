import * as Sentry from "@sentry/nextjs";

import { AgentError, HEADER, agent } from "@/src/server/agent";
import { logLine, newRunId } from "@/src/server/observability";

/** One Sentry test event from each service under one run id (T38, docs/ops.md): the web's,
 * then the agent's through `POST /ops/sentry-test`. Guarded by the internal key like the
 * seed revalidation route; outside proxy.ts's matcher, so never rate-limited. */
export async function POST(req: Request): Promise<Response> {
  const key = process.env.INTERNAL_API_KEY;
  if (!key || req.headers.get(HEADER) !== key) {
    return Response.json({ reason: "unauthorized" }, { status: 401 });
  }
  const runId = newRunId("req");
  console.info(logLine("sentry_test", runId));
  const webEventId = Sentry.withScope((scope) => {
    scope.setTag("run_id", runId);
    return Sentry.captureMessage("Outdraw web: Sentry test event", "info");
  });
  await Sentry.flush(2000);
  let agentEventId: string | null = null;
  try {
    agentEventId = (await agent.sentryTest(runId)).event_id;
  } catch (err) {
    if (!(err instanceof AgentError)) throw err;
  }
  return Response.json({
    run_id: runId,
    // An id is returned even without SENTRY_DSN; `sentry` says whether anything was sent.
    sentry: Boolean(Sentry.getClient()),
    web_event_id: webEventId,
    agent_event_id: agentEventId,
  });
}
