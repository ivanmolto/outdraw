"use client";

// What a page's error boundary (app/**/error.tsx) shows for an error thrown past the page's
// own handling: the `unknown` state with a retry, under the page chrome. The thrown error
// is never read for text: in production Next replaces a server error's message with a
// generic one, in development it keeps the original (which can name the agent's URL), and
// a client error's message is whatever threw. Only the digest is shown, to match the
// server log and the Sentry event (instrumentation.ts) for it.
import { ErrorState } from "./error-state";

interface Props {
  error: unknown;
  retry: () => void;
  /** The venue the page belongs to, when the URL names one. */
  venueId?: string;
}

/** Next's hash of a server error, the one safe thing to show from it. */
export function digestOf(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("digest" in error)) return null;
  const digest = (error as { digest?: unknown }).digest;
  return typeof digest === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(digest) ? digest : null;
}

export function PageError({ error, retry, venueId }: Props) {
  const digest = digestOf(error);
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className="mx-auto w-full max-w-3xl px-4 py-8">
        <ErrorState reason="unknown" details={{ venueId }} onRetry={retry} />
        {digest && (
          <p className="mt-3 text-xs text-ink/50">
            Error reference: <code className="font-mono">{digest}</code>
          </p>
        )}
      </div>
    </main>
  );
}
