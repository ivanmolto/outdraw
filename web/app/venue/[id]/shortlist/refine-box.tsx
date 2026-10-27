"use client";

// The message box under the shortlist: four chips that send a ready-made refinement at
// once, or the booker's own words. Each submit is a new run chained to the one on screen
// (POST /api/runs/{id}/refine), so the box is locked while it runs.
import { useId, useState } from "react";

import { Elapsed, Spinner } from "@/src/components/progress";
import { reasonText, type Reason } from "@/src/lib/api-client";
import { LIMITS } from "@/src/lib/schemas";

/** Chip label → the message it sends. */
export const CHIPS = [
  { label: "More emerging", message: "More emerging acts" },
  { label: "Younger crowd", message: "Our crowd skews younger" },
  { label: "Exclude tributes", message: "No tribute acts" },
  { label: "Bigger acts", message: "Bigger acts" },
] as const;

/** The agent's 422 (no change read from the message) arrives as invalid_input. */
function errorText(reason: Reason): string {
  return reason === "invalid_input"
    ? "Outdraw couldn't read a change from that. Name genres to add or drop, the size of act, recent bookings to leave out, or how many candidates to consider."
    : reasonText(reason);
}

export function RefineBox({
  pending,
  error,
  onRefine,
}: {
  pending: boolean;
  error: Reason | null;
  onRefine: (message: string) => void;
}) {
  const [message, setMessage] = useState("");
  const inputId = useId();
  const headingId = useId();
  const trimmed = message.trim();

  return (
    <section aria-labelledby={headingId} className="mt-8 rounded-card border border-line bg-surface p-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!pending && trimmed) onRefine(trimmed);
        }}
      >
        <h2 id={headingId} className="text-sm font-medium text-ink">
          Refine this shortlist
        </h2>
        <ul aria-label="Suggested refinements" className="mt-2 flex flex-wrap gap-2">
          {CHIPS.map((chip) => (
            <li key={chip.label}>
              <button
                type="button"
                disabled={pending}
                onClick={() => onRefine(chip.message)}
                className="rounded-full border border-ink/30 px-3 py-1 text-sm text-ink hover:bg-ink hover:text-paper disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-ink"
              >
                {chip.label}
              </button>
            </li>
          ))}
        </ul>
        <label htmlFor={inputId} className="mt-3 block text-sm text-ink/70">
          Or say what to change
        </label>
        <div className="mt-1 flex flex-col gap-2 sm:flex-row sm:items-start">
          <textarea
            id={inputId}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            maxLength={LIMITS.message}
            rows={2}
            disabled={pending}
            placeholder="e.g. no covers bands, cap it at 300 people"
            className="min-w-0 flex-1 resize-y rounded-md border border-ink/30 bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink/40 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={pending || !trimmed}
            className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper disabled:opacity-50"
          >
            Refine
          </button>
        </div>
      </form>
      {pending && (
        <div className="mt-3 flex items-center gap-3 text-sm">
          <Spinner className="text-ink" />
          <p className="text-ink" aria-live="polite">
            Re-ranking with your change. This can take up to two minutes.
          </p>
          <Elapsed className="text-ink/50" />
        </div>
      )}
      {error && !pending && (
        <p role="alert" className="mt-3 text-sm text-ink/80">
          {errorText(error)}
        </p>
      )}
    </section>
  );
}
