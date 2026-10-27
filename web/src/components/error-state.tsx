"use client";

// The one way an API `{reason}` is shown (src/lib/error-states.ts): a title, the message,
// the matches still to confirm for a conflict, and a next action: "Try again" where a retry
// can help (a button with `onRetry`, or a full reload of `retryHref` from a server page),
// and a link to where to go instead. Never a backend message, URL or stack.
import Link from "next/link";

import { errorState, type ErrorDetails, type Reason } from "@/src/lib/error-states";

interface Props {
  reason: Reason;
  details?: ErrorDetails;
  /** Runs the request again; shown only for a retryable reason. */
  onRetry?: () => void;
  /** Without `onRetry`: the page to reload for a retryable reason (its own path). */
  retryHref?: string;
  /** The page's own heading, or a heading inside a panel. */
  heading?: "h1" | "h2";
  /** Tighter spacing inside a panel. */
  compact?: boolean;
}

const BUTTON = "rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper";

export function ErrorState({
  reason,
  details = {},
  onRetry,
  retryHref,
  heading: Heading = "h1",
  compact = false,
}: Props) {
  const state = errorState(reason, details);
  return (
    <div
      role="alert"
      data-reason={state.reason}
      className={`rounded-card border border-line bg-surface ${compact ? "p-4" : "p-6"}`}
    >
      <Heading className={compact ? "font-semibold" : "display text-2xl"}>
        {state.title}
      </Heading>
      <p className="mt-2 text-sm text-ink/70">{state.message}</p>
      {state.pending.length > 0 && (
        <ul aria-label="Still to confirm" className="mt-2 list-disc pl-5 text-sm text-ink/70">
          {state.pending.map((key) => (
            <li key={key}>{key}</li>
          ))}
        </ul>
      )}
      <div className={`flex flex-wrap items-center gap-4 ${compact ? "mt-3" : "mt-4"}`}>
        {state.retryable && onRetry && (
          <button type="button" onClick={onRetry} className={BUTTON}>
            Try again
          </button>
        )}
        {state.retryable && !onRetry && retryHref && (
          // A plain anchor: a full reload, past the router cache, as the retry a server
          // page needs.
          <a href={retryHref} className={BUTTON}>
            Try again
          </a>
        )}
        {state.link && (
          <Link href={state.link.href} className="text-sm font-medium underline">
            {state.link.label}
          </Link>
        )}
      </div>
    </div>
  );
}
