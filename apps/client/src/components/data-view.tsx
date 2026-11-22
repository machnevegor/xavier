import { cn } from "cn";
import type { ReactNode } from "react";
import { isRecord } from "@/api/types.ts";
import { CodeBlock } from "@/components/code-block.tsx";
import { Disclosure } from "@/components/disclosure.tsx";
import {
  humanize,
  isScalar,
  presentJson,
  recoverJson,
  toCode,
} from "@/lib/json.ts";
import {
  fieldsOf,
  type JsonSchema,
  limitsOf,
  nestedOf,
  typeLabel,
  unwrapSchema,
} from "@/lib/schema.ts";
import { m } from "@/paraglide/messages.js";

const MAX_DEPTH = 3;
const TEXT_KEYS = new Set([
  "stdout",
  "stderr",
  "output",
  "text",
  "content",
  "preview",
  "body",
]);

const Muted = ({ children }: { children: ReactNode }) => (
  <span className="text-muted-foreground">{children}</span>
);

/** A field holding program text names its language beside it; plain output has none. */
function blockLanguage(
  name: string | undefined,
  siblings: Record<string, unknown> | undefined,
): string | undefined {
  if (name === "command") {
    return "bash";
  }
  if (name === "code" || name === "script") {
    return typeof siblings?.language === "string" ? siblings.language : "";
  }
  return name && TEXT_KEYS.has(name) ? "" : undefined;
}

const isRows = (list: unknown[]): list is Record<string, unknown>[] =>
  list.length > 1 &&
  list.every((row) => isRecord(row) && Object.values(row).every(isScalar)) &&
  new Set(list.flatMap((row) => (isRecord(row) ? Object.keys(row) : [])))
    .size <= 6;

const isBlock = (value: unknown): boolean =>
  isRecord(value) ||
  (Array.isArray(value) && value.some((item) => !isScalar(item))) ||
  (typeof value === "string" && (value.includes("\n") || value.length > 120));

function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="text-muted-foreground border-b">
            {columns.map((column) => (
              <th
                className="py-1.5 pr-4 font-medium whitespace-nowrap"
                key={column}
                scope="col"
              >
                {humanize(column)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr className="border-b last:border-0" key={index}>
              {columns.map((column) => (
                <td className="py-1.5 pr-4 align-top" key={column}>
                  {row[column] === null || row[column] === undefined
                    ? m.common_text()
                    : String(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface ViewProps {
  value: unknown;
  schema?: JsonSchema | undefined;
  root?: JsonSchema | undefined;
  depth?: number;
}

function DataValue({
  value,
  name,
  siblings,
  schema,
  root,
  depth = 0,
}: ViewProps & { name?: string; siblings?: Record<string, unknown> }) {
  if (value === null || value === undefined) {
    return <Muted>{m.common_text()}</Muted>;
  }
  if (typeof value === "boolean") {
    return value ? m.data_yes() : m.data_no();
  }
  if (typeof value === "number") {
    return <span className="tabular-nums">{String(value)}</span>;
  }
  if (typeof value === "string") {
    if (!value) {
      return <Muted>{m.data_empty()}</Muted>;
    }
    const language = blockLanguage(name, siblings);
    const recovered = language === undefined ? recoverJson(value) : undefined;
    if (recovered) {
      return (
        <CodeBlock
          code={presentJson(recovered)}
          copy={value}
          label="JSON"
          language="json"
        />
      );
    }
    if (
      language !== undefined ||
      value.includes("\n") ||
      /^[[{]/.test(value.trim())
    ) {
      const shown = toCode(value, language || undefined);
      return (
        <CodeBlock code={shown.code} copy={value} language={shown.language} />
      );
    }
    return (
      <span
        className={cn(
          "break-words",
          !/\s/.test(value) && "font-mono text-xs break-all",
        )}
      >
        {value}
      </span>
    );
  }
  if (depth >= MAX_DEPTH) {
    return <CodeBlock code={JSON.stringify(value, null, 2)} language="json" />;
  }
  if (!Array.isArray(value)) {
    return (
      <div className="border-l pl-3">
        <ValueView depth={depth} root={root} schema={schema} value={value} />
      </div>
    );
  }
  if (value.length === 0) {
    return <Muted>{m.data_none()}</Muted>;
  }
  if (value.every(isScalar)) {
    return (
      <span className="font-mono text-xs break-words">
        {value.map(String).join(", ")}
      </span>
    );
  }
  if (isRows(value)) {
    return <DataTable rows={value} />;
  }
  const items =
    schema?.items && root ? unwrapSchema(schema.items, root) : undefined;
  return (
    <ol className="space-y-2">
      {value.map((item, index) => (
        <li className="border-l pl-3" key={index}>
          <ValueView depth={depth} root={root} schema={items} value={item} />
        </li>
      ))}
    </ol>
  );
}

/**
 * Structured data laid out by its shape: labelled fields, tables for like records, code
 * blocks for program text. A schema, when given, supplies field titles and descriptions.
 */
export function ValueView({
  value,
  schema,
  root = schema,
  depth = 0,
}: ViewProps) {
  if (!isRecord(value)) {
    return (
      <DataValue depth={depth} root={root} schema={schema} value={value} />
    );
  }
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return <Muted>{m.data_empty()}</Muted>;
  }
  const fields =
    schema && root ? (unwrapSchema(schema, root).properties ?? {}) : {};
  return (
    <dl className="space-y-2">
      {entries.map(([key, item]) => {
        const declared = fields[key];
        const field =
          declared && root ? unwrapSchema(declared, root) : undefined;
        const block = isBlock(item);
        return (
          <div
            className={cn(
              !block && "sm:grid sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-x-4",
            )}
            key={key}
          >
            <dt
              className="text-muted-foreground text-sm"
              title={field?.description}
            >
              {field?.title ?? humanize(key)}
            </dt>
            <dd className={cn("min-w-0 text-sm", block && "mt-1")}>
              <DataValue
                depth={depth + 1}
                name={key}
                root={root}
                schema={field}
                siblings={value}
                value={item}
              />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** The untouched record, for when the readable view is not enough. */
export function RawJson({
  value,
  label = m.data_view_raw_json(),
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <Disclosure contentClassName="pb-1" label={label}>
      <CodeBlock code={JSON.stringify(value, null, 2)} language="json" />
    </Disclosure>
  );
}

function Fields({
  schema,
  root,
  depth,
}: {
  schema: JsonSchema;
  root: JsonSchema;
  depth: number;
}) {
  return (
    <ul className="divide-y border-y">
      {fieldsOf(schema, root).map(({ name, field, required }) => {
        const limits = limitsOf(field);
        const nested = depth < 2 ? nestedOf(field, root) : null;
        return (
          <li className="py-2" key={name}>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <span className="font-mono text-sm">{name}</span>
              <span className="text-muted-foreground text-sm">
                {typeLabel(field, root)}
              </span>
              <span
                className={
                  required
                    ? "text-xs font-medium"
                    : "text-muted-foreground text-xs"
                }
              >
                {required ? m.data_required() : m.data_optional()}
              </span>
            </div>
            {field.description && (
              <p className="text-muted-foreground mt-0.5 text-sm">
                {field.description}
              </p>
            )}
            {Array.isArray(field.enum) && (
              <p className="mt-0.5 font-mono text-xs break-words">
                {field.enum.map(String).join(" / ")}
              </p>
            )}
            {limits.length > 0 && (
              <p className="text-muted-foreground mt-0.5 text-xs">
                {limits.join(" / ")}
              </p>
            )}
            {nested && (
              <div className="mt-2 border-l pl-3">
                <Fields depth={depth + 1} root={root} schema={nested} />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** A JSON Schema as a field list; what the schema leaves out is not filled in. */
export function SchemaReference({ schema }: { schema: JsonSchema }) {
  const listed = fieldsOf(schema, schema).length > 0;
  return (
    <div>
      {listed ? (
        <Fields depth={0} root={schema} schema={schema} />
      ) : (
        <p className="text-muted-foreground text-sm">
          {m.data_this_schema_does_not_list_its_fields()}
        </p>
      )}
      <RawJson value={schema} />
    </div>
  );
}
