// Shared plumbing for the /api route handlers: validate input with zod, call the agent,
// and turn every failure into a `{reason}` JSON body the UI can branch on.
import "server-only";

import type { z } from "zod";

import { idSchema } from "@/src/lib/schemas";
import { AgentError, type Reason } from "./agent";

/** Request bodies above this are rejected before parsing (a full venue is ~3 KB). A sanity
 * cap on the decoded text, not a byte limit: the platform bounds the raw request. */
const MAX_BODY_CHARS = 32_000;

export interface ErrorBody {
  reason: Reason;
  /** Dotted path of the first invalid field, for invalid_input. */
  field?: string;
  /** Resolution keys still to confirm, for conflict on profile. */
  pending?: string[];
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

/** Run `fn` and answer with its JSON, or with `{reason}` on any failure. */
export async function proxy<T>(fn: () => Promise<T>, status = 200): Promise<Response> {
  try {
    return Response.json(await fn(), { status });
  } catch (err) {
    if (err instanceof InvalidInput) {
      return fail(400, { reason: "invalid_input", ...(err.field && { field: err.field }) });
    }
    if (err instanceof AgentError) {
      return fail(err.status, { reason: err.reason, ...(err.pending && { pending: err.pending }) });
    }
    console.error("api proxy failed:", err);
    return fail(500, { reason: "unknown" });
  }
}
