// What a refinement changed in the shortlist: each act of the new list marked as entered,
// moved (with how many places) or unchanged, plus the acts that left. Acts are matched by
// Qloo entity id, ranks are 1-based positions in the agent's order; nothing here sorts.
import type { ShortlistEntry } from "./run-result";

export type ActChange =
  | { kind: "entered" }
  | { kind: "same" }
  /** `by` > 0 moved up that many places, < 0 down. */
  | { kind: "moved"; from: number; by: number };

type Act = Pick<ShortlistEntry, "entity_id">;

export interface ShortlistDiff<T extends Act = ShortlistEntry> {
  /** By entity id, for every act in the new list. */
  changes: Map<string, ActChange>;
  /** Acts of the old list missing from the new one, in their old order. */
  left: { entry: T; rank: number }[];
}

export function diffShortlists<T extends Act>(
  before: readonly T[],
  after: readonly T[],
): ShortlistDiff<T> {
  const oldRank = new Map(before.map((e, i) => [e.entity_id, i + 1]));
  const kept = new Set(after.map((e) => e.entity_id));
  const changes = new Map<string, ActChange>();
  after.forEach((e, i) => {
    const from = oldRank.get(e.entity_id);
    const rank = i + 1;
    changes.set(
      e.entity_id,
      from === undefined
        ? { kind: "entered" }
        : from === rank
          ? { kind: "same" }
          : { kind: "moved", from, by: from - rank },
    );
  });
  const left = before.flatMap((entry, i) =>
    kept.has(entry.entity_id) ? [] : [{ entry, rank: i + 1 }],
  );
  return { changes, left };
}
