import Papa from "papaparse";
import { m } from "@/paraglide/messages.js";

export interface Table {
  columns: string[];
  rows: string[][];
  /** Rows the parser had to guess at: stray quotes, too few or too many fields. */
  problems: number;
}

export interface Sort {
  column: number;
  descending: boolean;
}

/** CSV or TSV text as a table whose first row names the columns; null when there is none. */
export function parseCsv(text: string): Table | null {
  const result = Papa.parse<string[]>(text.trim(), { skipEmptyLines: true });
  const [head, ...rows] = result.data;
  if (!head || head.length === 0) {
    return null;
  }
  return {
    columns: head.map(
      (name, index) => name.trim() || m.data_column({ n: index + 1 }),
    ),
    problems: result.errors.length,
    rows,
  };
}

const numeric = (cell: string): number | null => {
  const trimmed = cell.trim();
  const value = Number(trimmed);
  return trimmed !== "" && Number.isFinite(value) ? value : null;
};

/** Numbers by value, everything else as text with embedded numbers in order. */
export function compareCells(a: string, b: string): number {
  const x = numeric(a);
  const y = numeric(b);
  if (x !== null && y !== null) {
    return x - y;
  }
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

/**
 * The rows to show: sorted, at most `limit` of them, each with its position in the source so
 * it keeps a stable key.
 */
export function viewRows(
  table: Table,
  sort: Sort | null,
  limit: number,
): { rows: { index: number; cells: string[] }[]; matching: number } {
  const rows = table.rows.map((cells, index) => ({ cells, index }));
  if (sort) {
    rows.sort((a, b) => {
      const order = compareCells(
        a.cells[sort.column] ?? "",
        b.cells[sort.column] ?? "",
      );
      return sort.descending ? -order : order;
    });
  }
  return { matching: rows.length, rows: rows.slice(0, limit) };
}
