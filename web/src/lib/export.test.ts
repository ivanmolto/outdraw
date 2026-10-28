import { describe, expect, it } from "vitest";

import {
  CSV_COLUMNS,
  EXPORT_NOTE,
  UNDATED,
  csvField,
  exportFileName,
  exportRows,
  holdSummary,
  toCsv,
  type ExportAct,
} from "./export";

const acts: ExportAct[] = [
  { entityId: "E1", name: "Synthetic Act", total: 0.8123, topReason: "Strong crowd affinity" },
  { entityId: "E2", name: "Fixture, Duo", total: 0.7, topReason: 'Sold out "last" spring' },
  { entityId: "E3", name: "Mock Trio", total: 0.65, topReason: null },
  { entityId: "E4", name: "Placeholder Band", total: 0.6, topReason: "Line one\nline two" },
];

/** A strict RFC 4180 reader, as a spreadsheet would read the file. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\r" && text[i + 1] === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i++;
    } else field += c;
  }
  expect(quoted).toBe(false);
  expect(field).toBe("");
  return rows;
}

describe("exportRows and toCsv", () => {
  it("writes the six columns in order, one row per act in shortlist order", () => {
    const rows = parseCsv(toCsv(exportRows(acts, {})));

    expect(rows[0]).toEqual([...CSV_COLUMNS]);
    expect(rows[0]).toEqual([
      "act",
      "qloo_id",
      "score",
      "top_reason",
      "pencilled_date",
      "support_act",
    ]);
    expect(rows.slice(1).map((r) => r[1])).toEqual(["E1", "E2", "E3", "E4"]);
    expect(rows.every((r) => r.length === 6)).toBe(true);
    expect(rows[1]).toEqual(["Synthetic Act", "E1", "0.8123", "Strong crowd affinity", "", ""]);
    expect(rows[3][3]).toBe("");
  });

  it("includes pencilled dates, and marks a hold without a night", () => {
    const rows = parseCsv(toCsv(exportRows(acts, { E1: "2026-11-14", E3: null })));

    expect(rows.map((r) => r[4])).toEqual(["pencilled_date", "2026-11-14", "", UNDATED, ""]);
  });

  it("fills support acts where a brief drafted one", () => {
    const rows = parseCsv(toCsv(exportRows(acts, {}, { E2: "Sample Opener" })));

    expect(rows.map((r) => r[5])).toEqual(["support_act", "", "Sample Opener", "", ""]);
  });

  it("escapes commas, quotes and line breaks so each value reads back whole", () => {
    const csv = toCsv(exportRows(acts, {}));

    expect(csv).toContain('"Fixture, Duo"');
    expect(csv).toContain('"Sold out ""last"" spring"');
    const rows = parseCsv(csv);
    expect(rows).toHaveLength(5);
    expect(rows[2][0]).toBe("Fixture, Duo");
    expect(rows[2][3]).toBe('Sold out "last" spring');
    expect(rows[4][3]).toBe("Line one\nline two");
  });

  it("ends every line with CRLF", () => {
    const csv = toCsv(exportRows(acts.slice(0, 1), {}));

    expect(csv).toBe(
      "act,qloo_id,score,top_reason,pencilled_date,support_act\r\n" +
        "Synthetic Act,E1,0.8123,Strong crowd affinity,,\r\n",
    );
  });
});

describe("csvField", () => {
  it("leaves plain values alone", () => {
    expect(csvField("Synthetic Act")).toBe("Synthetic Act");
    expect(csvField("")).toBe("");
  });

  it("keeps a spreadsheet from reading a value as a formula", () => {
    expect(csvField("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvField("@act")).toBe("'@act");
    expect(csvField("+1,2")).toBe("\"'+1,2\"");
  });

  it("leaves a leading dash alone: a reason can open with one", () => {
    expect(csvField("- a strong local draw")).toBe("- a strong local draw");
  });
});

describe("holdSummary", () => {
  const rows = exportRows(acts, { E3: "2026-11-14", E1: "2026-12-05", E4: null }, {
    E1: "Sample Opener",
  });
  const text = holdSummary("Synthetic Room", rows, "2026-10-28");

  it("says it is an export, not a live integration", () => {
    expect(text).toContain("Pencil holds: Synthetic Room");
    expect(text).toContain("Exported from Outdraw on Wed, 28 Oct 2026.");
    expect(text).toContain(EXPORT_NOTE);
  });

  it("lists the pencilled acts by night with their dates, undated last", () => {
    const nights = text.split("\n").filter((l) => /^\S.*\(#\d/.test(l));

    expect(nights).toEqual([
      "Sat, 14 Nov 2026: Mock Trio (#3 on the shortlist, score 0.6500)",
      "Sat, 5 Dec 2026: Synthetic Act (#1 on the shortlist, score 0.8123)",
      "Night to be set: Placeholder Band (#4 on the shortlist, score 0.6000)",
    ]);
    expect(text).toContain("  Support: Sample Opener");
    expect(text).toContain("  Why: Strong crowd affinity");
    expect(text).not.toContain("Fixture, Duo");
  });

  it("says so when nothing is pencilled", () => {
    expect(holdSummary("Synthetic Room", exportRows(acts, {}), "2026-10-28")).toContain(
      "No acts pencilled on this shortlist yet.",
    );
  });
});

describe("exportFileName", () => {
  it("slugs the venue name and dates the file", () => {
    expect(exportFileName("Café Ödeon & Bar", "shortlist", "2026-10-28")).toBe(
      "outdraw-cafe-odeon-bar-shortlist-2026-10-28.csv",
    );
    expect(exportFileName("!!!", "holds", "2026-10-28")).toBe(
      "outdraw-venue-holds-2026-10-28.txt",
    );
  });
});
