import {
  IconArrowDown,
  IconArrowsSort,
  IconArrowUp,
} from "@tabler/icons-react";
import { cn } from "cn";
import { useMemo, useState } from "react";
import { CodeBlock } from "@/components/code-block.tsx";
import { VizBlock } from "@/components/viz/viz-block.tsx";
import { parseCsv, type Sort, type Table, viewRows } from "@/lib/csv.ts";
import { m } from "@/paraglide/messages.js";

/** Rows rendered at once; sorting reaches all of them. */
const SHOWN = { expanded: 1000, inline: 200 };

/** A table that sorts by any column, rendering a bounded slice. */
export function CsvTable({
  table,
  expanded = false,
}: {
  table: Table;
  expanded?: boolean;
}) {
  const [sort, setSort] = useState<Sort | null>(null);
  const limit = expanded ? SHOWN.expanded : SHOWN.inline;
  const { rows, matching } = useMemo(
    () => viewRows(table, sort, limit),
    [table, sort, limit],
  );
  const toggle = (column: number) =>
    setSort(
      sort?.column !== column
        ? { column, descending: false }
        : sort.descending
          ? null
          : { column, descending: true },
    );

  return (
    <div className={cn("flex flex-col", expanded && "h-full")}>
      <div
        className={cn(
          "overflow-auto",
          expanded ? "min-h-0 flex-1" : "max-h-96",
        )}
      >
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-muted sticky top-0 z-10">
            <tr>
              {table.columns.map((column, index) => {
                const active = sort?.column === index;
                const Icon = !active
                  ? IconArrowsSort
                  : sort.descending
                    ? IconArrowDown
                    : IconArrowUp;
                return (
                  <th
                    aria-sort={
                      active
                        ? sort.descending
                          ? "descending"
                          : "ascending"
                        : undefined
                    }
                    className="border-b p-0 font-medium"
                    key={index}
                    scope="col"
                  >
                    <button
                      className="hover:text-foreground text-muted-foreground flex w-full items-center gap-1 px-3 py-2 text-left whitespace-nowrap"
                      onClick={() => toggle(index)}
                      type="button"
                    >
                      {column}
                      <Icon
                        aria-hidden="true"
                        className={cn("size-3", !active && "opacity-40")}
                      />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                className="hover:bg-muted/50 border-b last:border-0"
                key={row.index}
              >
                {table.columns.map((column, index) => (
                  <td
                    className="max-w-96 truncate px-3 py-1.5 align-top"
                    key={`${column}-${index.toString()}`}
                    title={row.cells[index]}
                  >
                    {row.cells[index] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-muted-foreground px-3 py-1.5 text-xs">
        {matching > rows.length
          ? m.data_showing_of_rows({ shown: rows.length, total: matching })
          : m.data_row({ count: matching })}
        {` ${m.data_column_2({ count: table.columns.length })}`}
        {table.problems > 0 &&
          ` ${m.data_rows_couldnt_be_read_correctly({ count: table.problems })}`}
      </p>
    </div>
  );
}

/** A `csv` or `tsv` fence as a table, or as code when it is not tabular. */
export function CsvBlock({
  code,
  language,
}: {
  code: string;
  language: string;
}) {
  const table = useMemo(() => parseCsv(code), [code]);
  if (!table || table.rows.length === 0) {
    return <CodeBlock code={code} language={language} />;
  }
  return (
    <VizBlock
      code={code}
      label={language === "tsv" ? "TSV" : "CSV"}
      language={language}
      previewLabel={m.data_table()}
    >
      {(expanded) => <CsvTable expanded={expanded} table={table} />}
    </VizBlock>
  );
}
