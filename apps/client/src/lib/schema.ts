import { isRecord } from "@/api/types.ts";
import { m } from "@/paraglide/messages.js";

/** A JSON Schema node as tool catalogs publish it; only the keywords read here are typed. */
export interface JsonSchema {
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  definitions?: Record<string, JsonSchema>;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  type?: string | string[];
  format?: string;
  enum?: unknown[];
  const?: unknown;
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  description?: string;
  title?: string;
  default?: unknown;
  pattern?: string;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minItems?: number;
  maxItems?: number;
}

export function asSchema(value: unknown): JsonSchema {
  return isRecord(value) ? value : {};
}

function resolve(schema: JsonSchema, root: JsonSchema): JsonSchema {
  const name = schema.$ref?.match(/^#\/(?:\$defs|definitions)\/(.+)$/)?.[1];
  return (name && (root.$defs?.[name] ?? root.definitions?.[name])) || schema;
}

const variants = (schema: JsonSchema): JsonSchema[] =>
  (schema.anyOf ?? schema.oneOf ?? []).filter(
    (option) => option.type !== "null",
  );

/** "X or nothing" is how optional fields are written; read it as X. */
export function unwrapSchema(schema: JsonSchema, root: JsonSchema): JsonSchema {
  const resolved = resolve(schema, root);
  const options = variants(resolved);
  const [only] = options;
  return options.length === 1 && only
    ? {
        ...resolve(only, root),
        default: resolved.default,
        description: resolved.description,
      }
    : resolved;
}

const TYPES: Record<string, () => string> = {
  boolean: m.data_yes_or_no,
  integer: m.data_whole_number,
  number: m.data_number,
  object: m.data_group_of_fields,
  string: m.data_text_2,
};

export function typeLabel(schema: JsonSchema, root: JsonSchema): string {
  const s = unwrapSchema(schema, root);
  if (Array.isArray(s.enum)) {
    return m.data_one_of_a_fixed_set();
  }
  if (s.const !== undefined) {
    return m.data_always({ value: JSON.stringify(s.const) });
  }
  const options = variants(s);
  if (options.length > 0) {
    return options
      .map((option) => typeLabel(option, root))
      .join(` ${m.data_or()} `);
  }
  const type = Array.isArray(s.type)
    ? s.type.find((item) => item !== "null")
    : s.type;
  if (type === "array") {
    return s.items
      ? m.data_list_of({ type: typeLabel(s.items, root) })
      : m.data_list();
  }
  if (type === "string" && s.format) {
    return m.data_text_3({ format: s.format });
  }
  return (type && TYPES[type]?.()) ?? m.data_any_value();
}

export function limitsOf(schema: JsonSchema): string[] {
  const limits: string[] = [];
  if (schema.minLength !== undefined) {
    limits.push(m.data_at_least_characters({ count: schema.minLength }));
  }
  if (schema.maxLength !== undefined) {
    limits.push(m.data_at_most_characters({ count: schema.maxLength }));
  }
  if (schema.minimum !== undefined) {
    limits.push(m.data_minimum({ n: schema.minimum }));
  }
  if (schema.maximum !== undefined) {
    limits.push(m.data_maximum({ n: schema.maximum }));
  }
  if (schema.exclusiveMinimum !== undefined) {
    limits.push(m.data_more_than({ n: schema.exclusiveMinimum }));
  }
  if (schema.exclusiveMaximum !== undefined) {
    limits.push(m.data_less_than({ n: schema.exclusiveMaximum }));
  }
  if (schema.minItems !== undefined) {
    limits.push(m.data_at_least_items({ count: schema.minItems }));
  }
  if (schema.maxItems !== undefined) {
    limits.push(m.data_at_most_items({ count: schema.maxItems }));
  }
  if (schema.pattern) {
    limits.push(m.data_must_match({ pattern: schema.pattern }));
  }
  if (schema.default !== undefined && schema.default !== null) {
    limits.push(m.data_default({ value: JSON.stringify(schema.default) }));
  }
  return limits;
}

/** The fields a schema lists, in its own order, each already unwrapped. */
export function fieldsOf(
  schema: JsonSchema,
  root: JsonSchema,
): { name: string; field: JsonSchema; required: boolean }[] {
  const s = unwrapSchema(schema, root);
  if (!s.properties) {
    return [];
  }
  const required = new Set(s.required ?? []);
  return Object.entries(s.properties).map(([name, field]) => ({
    field: unwrapSchema(field, root),
    name,
    required: required.has(name),
  }));
}

/** The schema whose fields describe one entry: the object itself, or a list's items. */
export function nestedOf(
  schema: JsonSchema,
  root: JsonSchema,
): JsonSchema | null {
  const inner =
    schema.type === "array" && schema.items
      ? unwrapSchema(schema.items, root)
      : schema;
  return inner.properties ? inner : null;
}
