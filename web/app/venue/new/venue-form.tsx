"use client";

// Step 1 of /venue/new: the venue and its past shows. The draft lives in the parent so it
// survives a trip back from the confirm step; validation uses the T17 zod schema.
import { useState, type FormEvent } from "react";

import { LivePaused } from "@/src/components/live-paused";
import { Elapsed, Spinner } from "@/src/components/progress";
import type { VenueUpsert } from "@/src/lib/schemas";
import type { SeedSlug } from "@/src/lib/seeds";
import {
  MAX_SHOWS,
  emptyShow,
  fieldLabel,
  parseDraft,
  type FieldErrors,
  type ShowDraft,
  type VenueDraft,
} from "@/src/lib/venue-draft";

/** A failed save: the message, and the field the server rejected, if any. */
export interface ServerError {
  message: string;
  field?: string;
  /** Matching was refused for want of live analysis: the demo to offer instead. */
  sample?: SeedSlug;
}

interface Props {
  draft: VenueDraft;
  onChange: (draft: VenueDraft) => void;
  onSubmit: (venue: VenueUpsert) => void;
  busy: boolean;
  serverError?: ServerError;
}

const fieldId = (path: string) => `f-${path.replaceAll(".", "-")}`;

const INPUT =
  "block w-full min-w-0 rounded-md border border-ink/20 bg-surface px-3 py-2 text-base text-ink " +
  "placeholder:text-ink/40 focus:border-ink focus:outline-2 focus:outline-offset-1 focus:outline-ink " +
  "aria-[invalid=true]:border-danger";

function Field({
  path,
  label,
  value,
  error,
  onChange,
  type = "text",
  inputMode,
  placeholder,
  hint,
  className,
}: {
  path: string;
  label: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  type?: "text" | "date";
  inputMode?: "numeric" | "decimal" | "text";
  placeholder?: string;
  hint?: string;
  className?: string;
}) {
  const id = fieldId(path);
  const described = [error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(" ");
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-ink">
        {label}
      </label>
      <input
        id={id}
        name={path}
        type={type}
        inputMode={inputMode}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
        className={INPUT}
      />
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1 text-xs text-ink/60">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function VenueForm({ draft, onChange, onSubmit, busy, serverError }: Props) {
  const [attempted, setAttempted] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});

  // Once the user has tried to submit, re-check on every edit so messages clear as they fix.
  function update(next: VenueDraft) {
    onChange(next);
    if (attempted) {
      const result = parseDraft(next);
      setErrors(result.ok ? {} : result.errors);
    }
  }

  const setVenue = (field: "name" | "city" | "country" | "capacity") => (value: string) =>
    update({ ...draft, [field]: value });

  const setShow = (index: number, field: keyof Omit<ShowDraft, "key">) => (value: string) =>
    update({
      ...draft,
      past_shows: draft.past_shows.map((s, i) => (i === index ? { ...s, [field]: value } : s)),
    });

  const addShow = () => {
    if (draft.past_shows.length >= MAX_SHOWS) return;
    update({ ...draft, past_shows: [...draft.past_shows, emptyShow()] });
  };

  const removeShow = (index: number) =>
    update({ ...draft, past_shows: draft.past_shows.filter((_, i) => i !== index) });

  function submit(e: FormEvent) {
    e.preventDefault();
    setAttempted(true);
    const result = parseDraft(draft);
    if (!result.ok) {
      setErrors(result.errors);
      const first = Object.keys(result.errors)[0];
      if (first) document.getElementById(fieldId(first))?.focus();
      return;
    }
    setErrors({});
    onSubmit(result.data);
  }

  const shown: FieldErrors = { ...errors };
  if (serverError?.field && !shown[serverError.field]) {
    shown[serverError.field] = "Rejected by the server; check this value";
  }
  const errorCount = Object.keys(errors).length;
  const atMax = draft.past_shows.length >= MAX_SHOWS;

  return (
    <form onSubmit={submit} noValidate className="space-y-8">
      {errorCount > 0 && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm">
          <p className="font-medium text-danger">
            {errorCount === 1 ? "1 field needs attention" : `${errorCount} fields need attention`}
          </p>
          <ul className="mt-1 list-disc pl-5 text-danger">
            {Object.entries(errors).map(([path, msg]) => (
              <li key={path}>
                <a href={`#${fieldId(path)}`} className="underline">
                  {fieldLabel(path)}
                </a>
                : {msg}
              </li>
            ))}
          </ul>
        </div>
      )}
      {serverError?.sample ? (
        <LivePaused slug={serverError.sample}>
          Your venue is saved, but its acts can&apos;t be matched in Qloo right now. Save again
          later to pick up where you left off.
        </LivePaused>
      ) : (
        serverError && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm text-danger">
            {serverError.message}
          </p>
        )
      )}

      <section aria-labelledby="venue-heading" className="space-y-4">
        <h2 id="venue-heading" className="display text-xl text-ink">
          Venue
        </h2>
        <Field
          path="name"
          label="Venue name"
          value={draft.name}
          error={shown.name}
          onChange={setVenue("name")}
          placeholder="e.g. The Back Room"
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            path="city"
            label="City"
            value={draft.city}
            error={shown.city}
            onChange={setVenue("city")}
            placeholder="e.g. Brooklyn"
          />
          <Field
            path="country"
            label="Country"
            value={draft.country}
            error={shown.country}
            onChange={setVenue("country")}
            placeholder="e.g. US"
          />
        </div>
        <Field
          path="capacity"
          label="Capacity"
          value={draft.capacity}
          error={shown.capacity}
          onChange={setVenue("capacity")}
          inputMode="numeric"
          placeholder="e.g. 350"
          hint="Licensed capacity for standing shows."
          className="sm:max-w-48"
        />
      </section>

      <section aria-labelledby="shows-heading" className="space-y-4">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="shows-heading" className="display text-xl text-ink">
            Past shows
          </h2>
          <span className="text-sm text-ink/60">
            {draft.past_shows.length} of {MAX_SHOWS}
          </span>
        </div>

        {shown.past_shows && (
          <p className="text-sm text-danger" role="alert">
            {shown.past_shows}
          </p>
        )}

        {draft.past_shows.length === 0 ? (
          <div className="rounded-card border border-dashed border-ink/30 p-6 text-center">
            <p className="font-medium text-ink">No past shows yet</p>
            <p className="mt-1 text-sm text-ink/70">
              Add up to {MAX_SHOWS} acts you&apos;ve booked recently. Outdraw reads your crowd
              from who turned out for them, so the shows that sold best help most.
            </p>
            <button
              type="button"
              onClick={addShow}
              className="mt-4 rounded-md bg-ink px-4 py-2 text-sm font-medium text-paper"
            >
              Add a past show
            </button>
          </div>
        ) : (
          <ol className="space-y-4">
            {draft.past_shows.map((show, i) => {
              const p = `past_shows.${i}`;
              return (
                <li key={show.key}>
                  <fieldset className="relative rounded-card border border-line bg-surface p-4">
                    <legend className="sr-only">Show {i + 1}</legend>
                    <p aria-hidden className="mb-3 text-sm font-semibold text-ink">
                      Show {i + 1}
                    </p>
                    <button
                      type="button"
                      onClick={() => removeShow(i)}
                      aria-label={`Remove show ${i + 1}`}
                      className="absolute top-2 right-2 rounded-md px-2 py-1 text-sm text-ink/70 underline hover:text-ink"
                    >
                      Remove
                    </button>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Field
                        path={`${p}.act`}
                        label="Act"
                        value={show.act}
                        error={shown[`${p}.act`]}
                        onChange={setShow(i, "act")}
                        placeholder="Artist or band"
                        className="col-span-2"
                      />
                      <Field
                        path={`${p}.date`}
                        label="Date"
                        type="date"
                        value={show.date}
                        error={shown[`${p}.date`]}
                        onChange={setShow(i, "date")}
                        className="col-span-2"
                      />
                      <Field
                        path={`${p}.tickets_sold`}
                        label="Tickets sold"
                        value={show.tickets_sold}
                        error={shown[`${p}.tickets_sold`]}
                        onChange={setShow(i, "tickets_sold")}
                        inputMode="numeric"
                      />
                      <Field
                        path={`${p}.capacity`}
                        label="Capacity"
                        value={show.capacity}
                        error={shown[`${p}.capacity`]}
                        onChange={setShow(i, "capacity")}
                        inputMode="numeric"
                      />
                      <Field
                        path={`${p}.ticket_price`}
                        label="Ticket price"
                        value={show.ticket_price}
                        error={shown[`${p}.ticket_price`]}
                        onChange={setShow(i, "ticket_price")}
                        inputMode="decimal"
                      />
                      <Field
                        path={`${p}.gross`}
                        label="Gross"
                        value={show.gross}
                        error={shown[`${p}.gross`]}
                        onChange={setShow(i, "gross")}
                        inputMode="decimal"
                      />
                    </div>
                  </fieldset>
                </li>
              );
            })}
          </ol>
        )}

        {draft.past_shows.length > 0 && (
          <div>
            <button
              type="button"
              onClick={addShow}
              disabled={atMax}
              className="w-full rounded-md border border-ink/30 px-4 py-2 text-sm font-medium text-ink disabled:opacity-50 sm:w-auto"
            >
              Add another show
            </button>
            {atMax ? (
              <p className="mt-2 text-sm text-ink/70">
                That&apos;s the maximum of {MAX_SHOWS}. Keep the most recent or best-selling.
              </p>
            ) : (
              <p className="mt-2 text-xs text-ink/60">
                Only act and date are required; tickets and gross sharpen the read.
              </p>
            )}
          </div>
        )}
      </section>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
        <button
          type="submit"
          disabled={busy}
          aria-busy={busy}
          className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-ink px-4 py-3 text-base font-semibold text-paper disabled:opacity-60 sm:w-auto"
        >
          {busy && <Spinner />}
          {busy ? "Matching in Qloo…" : "Save and match acts"}
        </button>
        {busy && <Elapsed className="text-sm text-ink/50" />}
      </div>
    </form>
  );
}
