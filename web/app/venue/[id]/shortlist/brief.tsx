"use client";

// The Soundcheck panel's Brief tab: the agent's booking brief for one act (why it fits,
// each bullet linked to the trail item it cites; night format; support act; partners; the
// outreach email with copy buttons), and the "Pencil hold" switch in the panel header.
// Every field carries the agent's own source label (`source_labels`), as in the trail.
import { useEffect, useId, useState } from "react";

import { Spinner } from "@/src/components/progress";
import { LivePaused } from "@/src/components/live-paused";
import {
  ApiError,
  api,
  reasonText,
  sampleSeed,
  type BriefOut,
  type Reason,
} from "@/src/lib/api-client";
import type { SeedSlug } from "@/src/lib/seeds";
import { usePrecomputed } from "@/src/lib/precomputed";
import type { SourceLabel } from "@/types/schemas";
import { Provenance } from "./provenance";

type BriefField = "why_it_fits" | "night_format" | "support_act" | "partners" | "outreach_email";

function Field({
  title,
  source,
  children,
}: {
  title: string;
  source: SourceLabel | undefined;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="border-t border-line pt-3">
      <div className="flex items-start justify-between gap-3">
        <h3 id={id} className="text-sm font-semibold text-ink">
          {title}
        </h3>
        <Provenance source={source} />
      </div>
      <div className="mt-1 text-sm text-ink/80">{children}</div>
    </section>
  );
}

type Copied = "idle" | "copied" | "failed";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<Copied>("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 2000);
    return () => clearTimeout(t);
  }, [state]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={copy}
        className="rounded-md border border-ink/20 px-2 py-0.5 text-xs font-medium text-ink"
      >
        {label}
      </button>
      <span role="status" className="text-xs text-ink/60">
        {state === "copied" ? "Copied" : state === "failed" ? "Couldn't copy; select the text" : ""}
      </span>
    </span>
  );
}

/** The tab's content for a loaded brief. No fetching: the tests render it from a fixture.
 * `onEvidence` opens the trail at a cited ref; refs `linkable` rejects (the support call,
 * which is not in the trail) show as plain text. */
export function Brief({
  data,
  onEvidence,
  linkable = () => false,
}: {
  data: BriefOut;
  onEvidence?: (ref: string) => void;
  linkable?: (ref: string) => boolean;
}) {
  const { brief, evidence = {}, support } = data;
  const labels = (data.source_labels ?? {}) as Partial<Record<BriefField, SourceLabel>>;
  const email = brief.outreach_email;
  const supportName = brief.support_act ?? support.name;

  return (
    <div className="space-y-4">
      {data.error && (
        <p
          role="note"
          className="rounded-md border border-dashed border-ink/40 p-2 text-xs text-ink/70"
        >
          This is Outdraw&apos;s template: the drafted brief didn&apos;t cite its evidence, so it
          was set aside. Reopen the tab to try drafting again.
        </p>
      )}

      <Field title="Why it fits" source={labels.why_it_fits}>
        <ul aria-label="Why it fits" className="space-y-2">
          {brief.why_it_fits.map((reason, i) => {
            const item = evidence[reason.evidence_ref];
            const label = item?.label ?? reason.evidence_ref;
            return (
              <li key={i} data-testid="fit-reason">
                <p>{reason.text}</p>
                <p className="mt-0.5 flex items-start justify-between gap-3 text-xs text-ink/60">
                  {onEvidence && linkable(reason.evidence_ref) ? (
                    <button
                      type="button"
                      data-testid="evidence-link"
                      onClick={() => onEvidence(reason.evidence_ref)}
                      className="min-w-0 text-left break-words underline"
                    >
                      Evidence: {label}
                    </button>
                  ) : (
                    <span data-testid="evidence-text" className="min-w-0 break-words">
                      Evidence: {label}
                    </span>
                  )}
                  <Provenance source={item?.source} />
                </p>
              </li>
            );
          })}
        </ul>
      </Field>

      <Field title="Night format" source={labels.night_format}>
        <p>{brief.night_format}</p>
      </Field>

      <Field title="Support act" source={labels.support_act}>
        {supportName ? (
          <p>
            <span className="font-medium text-ink">{supportName}</span>
            {support.overlap != null && (
              <span className="text-ink/60">
                {" "}
                · shares <span className="font-mono">{Math.round(support.overlap * 100)}%</span> of
                the crowd&apos;s tags
              </span>
            )}
          </p>
        ) : (
          <p className="text-ink/60">{support.note ?? "No support act to suggest."}</p>
        )}
      </Field>

      <Field title="Partners" source={labels.partners}>
        {brief.partners.length === 0 ? (
          <p className="text-ink/60">No partner places in the crowd profile.</p>
        ) : (
          <ul aria-label="Partners" className="list-disc pl-5">
            {brief.partners.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </Field>

      <Field title="Outreach email" source={labels.outreach_email}>
        <div className="space-y-2">
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium tracking-wide text-ink/60 uppercase">
                Subject
              </span>
              <CopyButton text={email.subject} label="Copy subject" />
            </div>
            <p data-testid="email-subject" className="mt-0.5 font-medium break-words text-ink">
              {email.subject}
            </p>
          </div>
          <div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium tracking-wide text-ink/60 uppercase">Body</span>
              <CopyButton text={email.body} label="Copy body" />
            </div>
            <p
              data-testid="email-body"
              className="mt-0.5 rounded-md bg-surface-2 p-2 break-words whitespace-pre-wrap text-ink"
            >
              {email.body}
            </p>
          </div>
        </div>
      </Field>
    </div>
  );
}

// --- loading -----------------------------------------------------------------------------

type Load =
  | { name: "loading" }
  | { name: "done"; data: BriefOut }
  | { name: "failed"; reason: Reason }
  /** Refused for want of live analysis (busy, budget, LIVE_MODE=off). */
  | { name: "paused"; slug: SeedSlug }
  /** A seed act precompute drafted no brief for: the agent would refuse it, so no fetch. */
  | { name: "absent" };

/** Briefs by run and act. The agent caches a usable brief, so this does too; a template
 * (`error` set) is drafted again next time, as the agent would. */
const loaded = new Map<string, BriefOut>();

export function resetBriefCache() {
  loaded.clear();
}

/** A seed run's briefs come precomputed with the page (src/lib/precomputed.tsx): no fetch. */
function useBrief(runId: string, entityId: string, attempt: number): Load {
  const key = `${runId} ${entityId}`;
  const seed = usePrecomputed(runId);
  const [state, setState] = useState<{ key: string; load: Load }>(() => {
    const hit = loaded.get(key);
    return {
      key,
      load: hit ? { name: "done", data: hit } : { name: "loading" },
    };
  });
  useEffect(() => {
    if (seed || loaded.has(key)) return;
    let active = true;
    api.getBrief(runId, entityId).then(
      (data) => {
        if (!data.error) loaded.set(key, data);
        if (active) setState({ key, load: { name: "done", data } });
      },
      (err: unknown) => {
        if (active) {
          const slug = sampleSeed(err);
          const reason = err instanceof ApiError ? err.reason : "unavailable";
          setState({ key, load: slug ? { name: "paused", slug } : { name: "failed", reason } });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [key, runId, entityId, attempt, seed]);
  if (seed) {
    const pre = seed.briefs[entityId];
    return pre ? { name: "done", data: pre } : { name: "absent" };
  }
  return state.key === key ? state.load : { name: "loading" };
}

/** The Brief tab: fetched the first time it opens (the first brief per act costs a Qloo
 * call and a model call), then read from the cache. */
export function BriefTab({
  runId,
  entityId,
  onEvidence,
  linkable,
}: {
  runId: string;
  entityId: string;
  onEvidence?: (ref: string) => void;
  linkable?: (ref: string) => boolean;
}) {
  const [attempt, setAttempt] = useState(0);
  const load = useBrief(runId, entityId, attempt);
  if (load.name === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-ink/70">
        <Spinner /> Drafting the booking brief…
      </p>
    );
  }
  if (load.name === "absent") {
    return (
      <p role="status" className="text-sm text-ink/70">
        The demo has briefs for its top acts only. Refine the shortlist for a live run to draft
        this one.
      </p>
    );
  }
  if (load.name === "paused") {
    return (
      <LivePaused slug={load.slug}>
        This act&apos;s booking brief can&apos;t be drafted right now. The rest of its
        Soundcheck is here; open the brief again later.
      </LivePaused>
    );
  }
  if (load.name === "failed") {
    return (
      <div role="alert" className="text-sm">
        <p className="text-ink/80">{reasonText(load.reason)}</p>
        <button
          type="button"
          onClick={() => setAttempt((n) => n + 1)}
          className="mt-3 rounded-md bg-ink px-3 py-1.5 font-semibold text-paper"
        >
          Try again
        </button>
      </div>
    );
  }
  return <Brief data={load.data} onEvidence={onEvidence} linkable={linkable} />;
}

// --- pencil hold -------------------------------------------------------------------------

/** The "Pencil hold" switch. Optimistic: it flips at once, and flips back with the reason
 * when the PATCH fails. `onChange` hears only stored values; a new `initial` (the stored
 * value read again) resets it. While a save is in flight the switch stays enabled, so it
 * keeps focus, and ignores clicks. */
export function PencilToggle({
  runId,
  entityId,
  initial,
  onChange,
}: {
  runId: string;
  entityId: string;
  initial: boolean;
  onChange?: (pencilled: boolean) => void;
}) {
  const [on, setOn] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Reason | null>(null);
  // A new stored value resets the switch (state adjusted during render, not in an effect).
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setOn(initial);
  }

  const toggle = async () => {
    if (saving) return;
    const next = !on;
    setOn(next);
    setSaving(true);
    setError(null);
    try {
      const out = await api.setPencil(runId, entityId, { pencilled: next });
      setOn(out.pencilled);
      onChange?.(out.pencilled);
    } catch (err) {
      setOn(!next);
      setError(err instanceof ApiError ? err.reason : "unavailable");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-busy={saving}
        onClick={toggle}
        className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-sm font-medium ${
          on ? "border-stage bg-stage text-brand-amber" : "border-stage/30 text-ink"
        }`}
      >
        <span
          aria-hidden="true"
          className={`inline-block h-2.5 w-2.5 rounded-full ${on ? "bg-brand-amber" : "border border-ink/40"}`}
        />
        Pencil hold
      </button>
      {error && (
        <p role="alert" className="text-xs text-ink/80">
          {reasonText(error)}
        </p>
      )}
    </div>
  );
}
