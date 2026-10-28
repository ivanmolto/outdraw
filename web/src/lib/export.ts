// "Export to my booking tool" (T49): the shortlist as a CSV and the pencil holds as a
// plain-text summary, built in the browser from what the page already holds. A file the
// booker imports, not a live integration: nothing here talks to a booking tool. Rows keep
// the agent's order (the deterministic score); nothing here sorts the CSV.

export const CSV_COLUMNS = [
  "act",
  "qloo_id",
  "score",
  "top_reason",
  "pencilled_date",
  "support_act",
] as const;

export type ExportRow = Record<(typeof CSV_COLUMNS)[number], string>;

/** One shortlisted act as the export needs it. */
export interface ExportAct {
  entityId: string;
  name: string;
  total: number;
  topReason: string | null;
}

/** `pencilled_date` for an act pencilled without a night yet. */
export const UNDATED = "TBD";

export const EXPORT_NOTE =
  "A file export, not a live integration: nothing is sent to your booking tool.";

/**
 * The CSV rows, in shortlist order. `holds` maps a pencilled act to its night (YYYY-MM-DD)
 * or null; `support` maps an act to the support act of its booking brief, where one has
 * been drafted (drafting one costs Qloo and model calls, so the export never does).
 */
export function exportRows(
  acts: readonly ExportAct[],
  holds: Readonly<Record<string, string | null>>,
  support: Readonly<Record<string, string>> = {},
): ExportRow[] {
  return acts.map((a) => ({
    act: a.name,
    qloo_id: a.entityId,
    score: a.total.toFixed(4),
    top_reason: a.topReason ?? "",
    pencilled_date: a.entityId in holds ? (holds[a.entityId] ?? UNDATED) : "",
    support_act: support[a.entityId] ?? "",
  }));
}

/** One CSV field (RFC 4180): quoted when it holds a comma, quote or line break, quotes
 * doubled. A leading `=`, `+`, `@`, tab or CR gets a `'` so a spreadsheet reads text, not a
 * formula. Not `-`: a reason can open with a dash, and Numbers shows the apostrophe; the
 * score is ours and always a plain number. */
export function csvField(value: string): string {
  const safe = /^[=+@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

/** The CSV: a header row, then one row per act, CRLF line ends as spreadsheets expect. */
export function toCsv(rows: readonly ExportRow[]): string {
  const lines = [
    CSV_COLUMNS.join(","),
    ...rows.map((r) => CSV_COLUMNS.map((c) => csvField(r[c])).join(",")),
  ];
  return `${lines.join("\r\n")}\r\n`;
}

const DAY = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

function day(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return DAY.format(new Date(Date.UTC(y, m - 1, d)));
}

/**
 * The pencil-hold summary as plain text: the pencilled acts by night (undated ones last),
 * each with its score, support act and top reason. `exportedOn` is YYYY-MM-DD.
 */
export function holdSummary(
  venueName: string,
  rows: readonly ExportRow[],
  exportedOn: string,
): string {
  const held = rows
    .map((row, i) => ({ row, rank: i + 1 }))
    .filter(({ row }) => row.pencilled_date !== "")
    .sort((a, b) => {
      const da = a.row.pencilled_date === UNDATED ? "~" : a.row.pencilled_date;
      const db = b.row.pencilled_date === UNDATED ? "~" : b.row.pencilled_date;
      return da < db ? -1 : da > db ? 1 : a.rank - b.rank;
    });
  const title = `Pencil holds: ${venueName}`;
  const lines = [
    title,
    "=".repeat(title.length),
    `Exported from Outdraw on ${day(exportedOn)}.`,
    EXPORT_NOTE,
    "",
  ];
  if (held.length === 0) {
    lines.push("No acts pencilled on this shortlist yet.");
  } else {
    for (const { row, rank } of held) {
      const night = row.pencilled_date === UNDATED ? "Night to be set" : day(row.pencilled_date);
      lines.push(`${night}: ${row.act} (#${rank} on the shortlist, score ${row.score})`);
      if (row.support_act) lines.push(`  Support: ${row.support_act}`);
      if (row.top_reason) lines.push(`  Why: ${row.top_reason}`);
      lines.push(`  Qloo ID: ${row.qloo_id}`);
      lines.push("");
    }
    lines.pop();
  }
  lines.push(
    "",
    "Scores are Outdraw's; act IDs and signals are Qloo data. Holds are pencils, not bookings.",
  );
  return `${lines.join("\n")}\n`;
}

/** A file name from the venue's name: `outdraw-<venue>-<kind>-<date>.<ext>`. */
export function exportFileName(
  venueName: string,
  kind: "shortlist" | "holds",
  exportedOn: string,
): string {
  const slug =
    venueName
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "venue";
  return `outdraw-${slug}-${kind}-${exportedOn}.${kind === "shortlist" ? "csv" : "txt"}`;
}
