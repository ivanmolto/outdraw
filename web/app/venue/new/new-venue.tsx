"use client";

// /venue/new flow: form → POST /api/venues → POST /resolve → "Qloo match" confirm step →
// POST /profile → /venue/[id]. A trip back to the form keeps the venue id, so a re-save
// updates the same venue (the agent keeps matches for acts that did not change). The draft
// and id are kept in sessionStorage so an evicted phone tab does not lose the typing.
import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { ApiError, api, reasonMessage, type ResolutionItem } from "@/src/lib/api-client";
import type { VenueUpsert } from "@/src/lib/schemas";
import { emptyDraft, type VenueDraft } from "@/src/lib/venue-draft";
import { ConfirmMatches } from "./confirm-matches";
import { VenueForm } from "./venue-form";

type Step =
  | { name: "form" }
  | { name: "confirm"; venueName: string; items: ResolutionItem[] };

export const DRAFT_KEY = "outdraw:venue-draft";

interface Stored {
  draft: VenueDraft;
  venueId?: string;
}

function readStored(): Stored | null {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Stored>;
    if (!parsed.draft || !Array.isArray(parsed.draft.past_shows)) return null;
    return { draft: { ...emptyDraft(), ...parsed.draft }, venueId: parsed.venueId };
  } catch {
    return null;
  }
}

function writeStored(value: Stored | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(DRAFT_KEY);
    else window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(value));
  } catch {
    // Private mode or a full store: the form still works, it just won't survive a reload.
  }
}

const noop = () => () => {};

/** The page is prerendered, so the stored draft can only be read once hydrated. */
export function NewVenue() {
  const hydrated = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
  return hydrated ? <Flow /> : null;
}

function Flow() {
  const router = useRouter();
  const [draft, setDraft] = useState<VenueDraft>(() => readStored()?.draft ?? emptyDraft());
  const [venueId, setVenueId] = useState<string | undefined>(() => readStored()?.venueId);
  const [step, setStep] = useState<Step>({ name: "form" });
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<{ message: string; field?: string }>();

  useEffect(() => {
    writeStored({ draft, venueId });
  }, [draft, venueId]);

  async function save(venue: VenueUpsert) {
    setBusy(true);
    setServerError(undefined);
    try {
      const { id } = await api.upsertVenue({ ...venue, id: venueId });
      setVenueId(id);
      const resolved = await api.resolveVenue(id);
      setStep({ name: "confirm", venueName: venue.name, items: resolved.items });
      window.scrollTo({ top: 0 });
    } catch (err) {
      // The venue was deleted under us: create a fresh one next time.
      if (err instanceof ApiError && err.reason === "not_found") setVenueId(undefined);
      setServerError({
        message: reasonMessage(err),
        field: err instanceof ApiError ? err.field : undefined,
      });
    } finally {
      setBusy(false);
    }
  }

  function done(id: string) {
    writeStored(null);
    router.push(`/venue/${encodeURIComponent(id)}`);
  }

  if (step.name === "confirm" && venueId) {
    return (
      <ConfirmMatches
        // A fresh resolve (after editing) restarts the step's local state.
        key={step.items.map((i) => i.query).join("|")}
        venueId={venueId}
        venueName={step.venueName}
        items={step.items}
        onBack={() => setStep({ name: "form" })}
        onDone={() => done(venueId)}
      />
    );
  }

  return (
    <VenueForm
      draft={draft}
      onChange={setDraft}
      onSubmit={(v) => void save(v)}
      busy={busy}
      serverError={serverError}
    />
  );
}
