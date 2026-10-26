// Sentry on the server and edge runtimes (T38), only with SENTRY_DSN; events are scrubbed
// of venue names and messages by src/server/observability.ts. No browser SDK: the DSN
// would need a NEXT_PUBLIC_ variable, and every agent call is server-side anyway.
import * as Sentry from "@sentry/nextjs";

import { sentryOptions } from "@/src/server/observability";

export function register(): void {
  const options = sentryOptions();
  if (options && (process.env.NEXT_RUNTIME === "nodejs" || process.env.NEXT_RUNTIME === "edge")) {
    Sentry.init(options);
  }
}

/** Errors a route or page throws past its own handling (proxy() catches the agent's). */
export const onRequestError = Sentry.captureRequestError;
