// Shared plumbing for the /api route handlers: validate input with zod, call the agent,
// and turn every failure into a `{reason}` JSON body the UI can branch on. Failures that
// are ours (the agent down or erroring, a bug here) also go to Sentry, tagged with the
// request's run id.
import "server-only";

import * as Sentry from "@sentry/nextjs";
import type { z } from "zod";

import type { Plan } from "@/src/lib/entitlements";
import { idSchema } from "@/src/lib/schemas";
import { agentError, type Reason } from "./agent";
import { shouldReportUnavailable } from "./observability";

/** Request bodies above this are rejected before parsing (a full venue is ~3 KB). A sanity
 * cap on the decoded text, not a byte limit: the platform bounds the raw request. */
const MAX_BODY_CHARS = 32_000;

export interface ErrorBody {
  reason: Reason;
  /** Dotted path of the first invalid field, for invalid_input. */
  field?: string;
  /** Resolution keys still to confirm, for conflict on profile. */
  pending?: string[];
  /** The demo venue to show instead, for busy, budget, live_mode_off and plan_limit. */
  fallback_seed?: string;
  /** The plan that ran out of runs today and its daily allowance, for plan_limit. */
  plan?: Plan;
  runs_per_day?: number;
  /** The Qloo error code and whether a retry can help, for qloo_error. */
  code?: string;
  retryable?: boolean;
}

export function fail(status: number, body: ErrorBody): Response {
  return Response.json(body, { status });
}

class InvalidInput extends Error {
  constructor(readonly field?: string) {
    super("invalid_input");
  }
}

function check<T>(schema: z.ZodType<T>, value: unknown, prefix?: string): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    const path = parsed.error.issues[0]?.path.join(".");
    throw new InvalidInput([prefix, path].filter(Boolean).join(".") || undefined);
  }
  return parsed.data;
}

/** The JSON body of `req`, validated by `schema`. */
export async function body<T>(req: Request, schema: z.ZodType<T>): Promise<T> {
  const raw = await req.text();
  if (raw.length > MAX_BODY_CHARS) throw new InvalidInput();
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new InvalidInput();
  }
  return check(schema, json);
}

/** A venue, run or entity id from the URL. */
export function id(value: string, field: string): string {
  return check(idSchema, value, field);
}

/** Our failure, not the user's: to Sentry (a no-op without SENTRY_DSN), scrubbed there. */
function report(err: unknown, runId?: string): void {
  Sentry.withScope((scope) => {
    if (runId) scope.setTag("run_id", runId);
    Sentry.captureException(err);
  });
}

/** Run `fn` and answer with its JSON (or the Response it made itself, such as a 429 from
 * the plan limit), or with `{reason}` on any failure. */
export async function proxy<T>(fn: () => Promise<T | Response>, status = 200): Promise<Response> {
  try {
    const out = await fn();
    return out instanceof Response ? out : Response.json(out, { status });
  } catch (err) {
    if (err instanceof InvalidInput) {
      return fail(400, { reason: "invalid_input", ...(err.field && { field: err.field }) });
    }
    const agentErr = agentError(err);
    if (agentErr) {
      if (agentErr.reason === "unknown") report(agentErr, agentErr.runId);
      else if (agentErr.reason === "unavailable" && shouldReportUnavailable()) {
        report(agentErr, agentErr.runId);
      }
      return fail(agentErr.status, {
        reason: agentErr.reason,
        ...(agentErr.pending && { pending: agentErr.pending }),
        ...(agentErr.fallbackSeed && { fallback_seed: agentErr.fallbackSeed }),
        ...(agentErr.field && { field: agentErr.field }),
        ...(agentErr.code && { code: agentErr.code, retryable: agentErr.retryable === true }),
      });
    }
    console.error("api proxy failed:", err);
    report(err);
    return fail(500, { reason: "unknown" });
  }
}
