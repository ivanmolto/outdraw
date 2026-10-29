"use client";

// The message box under the shortlist: four chips that send a ready-made refinement at
// once, or the booker's own words. Each submit is a new run chained to the one on screen
// (POST /api/runs/{id}/refine), so the box is locked while it runs.
import { useId, useState } from "react";

import Link from "next/link";

import { Elapsed, Spinner } from "@/src/components/progress";
import { type Reason } from "@/src/lib/api-client";
import { errorState, type ErrorDetails } from "@/src/lib/error-states";
import type { SizeBand } from "@/src/lib/run-result";
import { LIMITS } from "@/src/lib/schemas";

/** A failed refinement: the reason and what came with it (`errorOf` in api-client.ts). */
export interface RefineError {
  reason: Reason;
  details: ErrorDetails;
}

/** Chip label → the message it sends. A chip with a `tier` asks for that size tier, so it
 * is hidden on a run already searching it (the agent would read no change and refuse). */
export const CHIPS: readonly { label: string; message: string; tier?: SizeBand }[] = [
  { label: "More emerging", message: "More emerging acts", tier: "emerging" },
  { label: "Younger crowd", message: "Our crowd skews younger" },
  { label: "Exclude tributes", message: "No tribute acts" },
  { label: "Bigger acts", message: "Bigger acts", tier: "established" },
];

/** The chips that can change `sizeTier`'s run. */
export function chipsFor(sizeTier: SizeBand | undefined) {
  return CHIPS.filter((chip) => !chip.tier || chip.tier !== sizeTier);
}

/** The agent's 422 (no change read from the message) arrives as invalid_input; the plan's
 * daily allowance (plan_limit) names the plan and links to the plans. */
function errorLine({ reason, details }: RefineError) {
  if (reason === "invalid_input") {
    return "Outdraw couldn't read a change from that. Name genres to add or drop, the size of act, recent bookings to leave out, or how many candidates to consider.";
  }
  const state = errorState(reason, details);
  if (reason !== "plan_limit" || !state.link) return state.message;
  return (
    <>
      {state.message}{" "}
      <Link href={state.link.href} className="font-medium underline">
        {state.link.label}
      </Link>
    </>
  );
}

export function RefineBox({
  pending,
  error,
  sizeTier,
  onRefine,
}: {
  pending: boolean;
  error: RefineError | null;
  /** The run's size tier, to leave out the chip that asks for it. */
  sizeTier?: SizeBand;
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
          {chipsFor(sizeTier).map((chip) => (
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
        <p role="alert" data-reason={error.reason} className="mt-3 text-sm text-ink/80">
          {errorLine(error)}
        </p>
      )}
    </section>
  );
}
