import { describe, expect, it } from "vitest";
import { compareCells, parseCsv, type Table, viewRows } from "@/lib/csv.ts";

describe("parseCsv", () => {
  it("reads a header row and its cells", () => {
    const table = parseCsv("name,age\nAda,36\nGrace,45\n");
    expect(table).toEqual({
      columns: ["name", "age"],
      problems: 0,
      rows: [
        ["Ada", "36"],
        ["Grace", "45"],
      ],
    });
  });

  it("names a blank header", () => {
    expect(parseCsv(",age\nAda,36\n")?.columns).toEqual(["Column 1", "age"]);
  });

  it("returns null for empty input", () => {
    expect(parseCsv("")).toBeNull();
    expect(parseCsv("\n\n")).toBeNull();
  });
});

describe("viewRows", () => {
  const table: Table = {
    columns: ["city", "pop"],
    problems: 0,
    rows: [
      ["Paris", "2"],
      ["Lyon", "0.5"],
      ["Lille", "0.2"],
    ],
  };

  it("sorts without mutating the source", () => {
    const { rows, matching } = viewRows(
      table,
      { column: 1, descending: true },
      10,
    );
    expect(matching).toBe(3);
    expect(rows.map((row) => row.cells[0])).toEqual(["Paris", "Lyon", "Lille"]);
    expect(table.rows[0]?.[0]).toBe("Paris");
  });

  it("caps the rendered slice", () => {
    const { rows, matching } = viewRows(table, null, 1);
    expect(matching).toBe(3);
    expect(rows).toHaveLength(1);
  });
});

describe("compareCells", () => {
  it("orders numbers by value and text alphabetically", () => {
    expect(compareCells("10", "2")).toBeGreaterThan(0);
    expect(compareCells("a", "b")).toBeLessThan(0);
  });
});
