"use client";

// Step 2 of /venue/new, labelled "Qloo match": resolve → confirm → ask. Each row is the venue
// or a past act with its Qloo match (auto-confirmed, editable) or a short list of candidates
// to pick from, plus "Not in Qloo" to skip it. A pick collapses the row at once and is saved
// through /confirm behind it (reverted with an error if that fails); building the profile
// waits for every row to be confirmed and every save to land.
import { useState } from "react";

import {
  ApiError,
  api,
  reasonMessage,
  type EntityCandidate,
  type ResolutionItem,
} from "@/src/lib/api-client";

interface Props {
  venueId: string;
  /** The venue name as typed; the venue row's `query` is an opaque key, never displayed. */
  venueName: string;
  items: ResolutionItem[];
  onBack: () => void;
  /** Called once the crowd profile is built. */
  onDone: () => void;
}

const SKIP = "__not_in_qloo__";

/** `urn:entity:artist` → `artist`. */
const typeLabel = (type: string) => type.split(":").at(-1) ?? type;

export function QlooBadge() {
  return (
    <span className="inline-block rounded-sm bg-ink px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-brand-amber uppercase">
      Qloo match
    </span>
  );
}

function MatchRow({
  item,
  label,
  editing,
  saving,
  disabled,
  error,
  onPick,
  onEdit,
  onCancel,
}: {
  item: ResolutionItem;
  label: string;
  editing: boolean;
  saving: boolean;
  disabled: boolean;
  error?: string;
  onPick: (entityId: string | null) => void;
  onEdit: () => void;
  onCancel: () => void;
}) {
  const kind = item.kind === "venue" ? "Venue" : "Act";
  const chosen = item.candidates.find((c) => c.entity_id === item.entity_id);

  if (item.confirmed && !editing) {
    return (
      <li className="rounded-lg border border-ink/15 bg-white/60 p-4" data-testid="match-row">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-ink/60">{kind}</p>
            <p className="font-medium break-words text-ink">{label}</p>
            {item.entity_id ? (
              <p className="mt-1 text-sm break-words text-ink/80">
                <span className="sr-only">Confirmed: </span>✓ {item.canonical_name}
                {chosen && <span className="text-ink/50"> · {typeLabel(chosen.type)}</span>}
              </p>
            ) : (
              <p className="mt-1 text-sm text-ink/70">Skipped: not in Qloo</p>
            )}
            {saving && <p className="mt-1 text-xs text-ink/50">Saving…</p>}
          </div>
          <button
            type="button"
            onClick={onEdit}
            disabled={disabled || saving}
            aria-label={`Change match for ${label}`}
            className="shrink-0 rounded-md px-2 py-1 text-sm text-ink/70 underline disabled:opacity-50"
          >
            Change
          </button>
        </div>
      </li>
    );
  }

  const name = `match-${item.query}`;
  const current = item.confirmed ? (item.entity_id ?? SKIP) : undefined;
  const options: (EntityCandidate | null)[] = [...item.candidates, null];

  return (
    <li
      className="rounded-lg border-2 border-ink/40 bg-white p-4"
      data-testid="match-row"
      data-pending={!item.confirmed || undefined}
    >
      <fieldset disabled={disabled}>
        <legend className="mb-2">
          <span className="block text-xs text-ink/60">{kind}</span>
          <span className="block font-medium break-words text-ink">{label}</span>
          <span className="mt-1 block text-sm text-ink/70">
            {item.candidates.length === 0
              ? "Qloo found no match. Skip it, or go back and check the spelling."
              : "Which one is it?"}
          </span>
        </legend>
        <div className="space-y-2">
          {options.map((c) => {
            const value = c ? c.entity_id : SKIP;
            const guess = c && !item.confirmed && c.entity_id === item.entity_id;
            return (
              <label
                key={value}
                className="flex cursor-pointer items-start gap-3 rounded-md border border-ink/15 p-3 has-checked:border-ink has-checked:bg-paper"
              >
                <input
                  type="radio"
                  name={name}
                  value={value}
                  checked={current === value}
                  onChange={() => onPick(c ? c.entity_id : null)}
                  className="mt-1 size-4 shrink-0 accent-ink"
                />
                <span className="min-w-0 text-sm">
                  {c ? (
                    <>
                      <span className="font-medium break-words text-ink">{c.name}</span>
                      <span className="text-ink/50"> · {typeLabel(c.type)}</span>
                      {guess && <span className="ml-1 text-xs text-ink/60">(best guess)</span>}
                    </>
                  ) : (
                    <span className="text-ink/80">Not in Qloo: skip it</span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
      {editing && (
        <button type="button" onClick={onCancel} className="mt-3 text-sm text-ink/70 underline">
          Keep current match
        </button>
      )}
    </li>
  );
}

export function ConfirmMatches({ venueId, venueName, items: initial, onBack, onDone }: Props) {
  const [items, setItems] = useState(initial);
  const [editing, setEditing] = useState<ReadonlySet<string>>(new Set());
  /** Rows whose /confirm call is still in flight. */
  const [saving, setSaving] = useState<ReadonlySet<string>>(new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const without = (set: ReadonlySet<string>, key: string) => {
    const next = new Set(set);
    next.delete(key);
    return next;
  };
  const withKey = (set: ReadonlySet<string>, key: string) => new Set(set).add(key);
  const replace = (query: string, next: ResolutionItem) =>
    setItems((prev) => prev.map((i) => (i.query === query ? next : i)));

  async function pick(query: string, entityId: string | null) {
    const before = items.find((i) => i.query === query);
    if (!before) return;
    const chosen = before.candidates.find((c) => c.entity_id === entityId);
    // Optimistic: show the pick now, the way the agent will echo it back.
    replace(query, {
      ...before,
      confirmed: true,
      entity_id: chosen?.entity_id ?? null,
      canonical_name: chosen?.name ?? before.canonical_name,
    });
    setEditing((prev) => without(prev, query));
    setSaving((prev) => withKey(prev, query));
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[query];
      return next;
    });
    try {
      replace(query, await api.confirmMatch(venueId, { query, chosen_entity_id: entityId }));
    } catch (err) {
      replace(query, before);
      if (before.confirmed) setEditing((prev) => withKey(prev, query));
      setRowErrors((prev) => ({ ...prev, [query]: reasonMessage(err) }));
    } finally {
      setSaving((prev) => without(prev, query));
    }
  }

  async function build() {
    setBuilding(true);
    setError(null);
    try {
      await api.buildProfile(venueId);
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.reason === "conflict" && err.pending) {
        const pending = new Set(err.pending);
        setItems((prev) => prev.map((i) => (pending.has(i.query) ? { ...i, confirmed: false } : i)));
      }
      setError(reasonMessage(err));
      setBuilding(false);
    }
  }

  const open = items.filter((i) => !i.confirmed || editing.has(i.query)).length;
  const ready = open === 0 && saving.size === 0;
  const acts = items.filter((i) => i.kind === "act").length;

  return (
    <section aria-labelledby="confirm-heading" className="space-y-6">
      <header className="space-y-2">
        <QlooBadge />
        <h2 id="confirm-heading" className="text-xl font-semibold text-ink">
          Confirm matches
        </h2>
        <p className="text-sm text-ink/70">
          We looked up your venue and past acts in Qloo&apos;s taste graph. Check each match,
          because Outdraw builds your crowd profile only from the ones you confirm.
        </p>
      </header>

      <ul className="space-y-3">
        {items.map((item) => (
          <MatchRow
            key={item.query}
            item={item}
            label={item.kind === "venue" ? venueName : item.query}
            editing={editing.has(item.query)}
            saving={saving.has(item.query)}
            disabled={building}
            error={rowErrors[item.query]}
            onPick={(id) => void pick(item.query, id)}
            onEdit={() => setEditing((prev) => withKey(prev, item.query))}
            onCancel={() => setEditing((prev) => without(prev, item.query))}
          />
        ))}
      </ul>

      {acts === 0 && (
        <p className="text-sm text-ink/70">
          No past acts to match. Add some shows to get a sharper crowd profile.
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-red-700/30 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center">
        <button
          type="button"
          onClick={onBack}
          disabled={building}
          className="rounded-md border border-ink/30 px-4 py-3 text-base font-medium text-ink disabled:opacity-50"
        >
          Back to edit
        </button>
        <button
          type="button"
          onClick={() => void build()}
          disabled={!ready || building}
          className="rounded-md bg-ink px-4 py-3 text-base font-semibold text-paper disabled:opacity-50"
        >
          {building ? "Building crowd profile…" : "Build crowd profile"}
        </button>
        <p className="text-sm text-ink/60" aria-live="polite">
          {building
            ? "This makes a few Qloo calls and can take up to a minute."
            : open > 0
              ? `${open} ${open === 1 ? "match" : "matches"} left to confirm`
              : saving.size > 0
                ? "Saving your picks…"
                : "All matches confirmed"}
        </p>
      </div>
    </section>
  );
}
