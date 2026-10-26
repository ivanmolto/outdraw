// Shared plumbing for the /api route handlers: validate input with zod, call the agent,
// and turn every failure into a `{reason}` JSON body the UI can branch on. Failures that
// are ours (the agent down or erroring, a bug here) also go to Sentry, tagged with the
// request's run id.
import "server-only";

import * as Sentry from "@sentry/nextjs";
import type { z } from "zod";

import { idSchema } from "@/src/lib/schemas";
import { AgentError, type Reason } from "./agent";
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
  /** The demo venue to show instead, for busy, budget and live_mode_off. */
  fallback_seed?: string;
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

/** Run `fn` and answer with its JSON, or with `{reason}` on any failure. */
export async function proxy<T>(fn: () => Promise<T>, status = 200): Promise<Response> {
  try {
    return Response.json(await fn(), { status });
  } catch (err) {
    if (err instanceof InvalidInput) {
      return fail(400, { reason: "invalid_input", ...(err.field && { field: err.field }) });
    }
    if (err instanceof AgentError) {
      if (err.reason === "unknown") report(err, err.runId);
      else if (err.reason === "unavailable" && shouldReportUnavailable()) report(err, err.runId);
      return fail(err.status, {
        reason: err.reason,
        ...(err.pending && { pending: err.pending }),
        ...(err.fallbackSeed && { fallback_seed: err.fallbackSeed }),
        ...(err.field && { field: err.field }),
      });
    }
    console.error("api proxy failed:", err);
    report(err);
    return fail(500, { reason: "unknown" });
  }
}
