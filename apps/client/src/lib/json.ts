import { isRecord } from "@/api/types.ts";

export const isScalar = (value: unknown): boolean =>
  value === null || ["string", "number", "boolean"].includes(typeof value);

/** A string that holds a JSON object or array, parsed; otherwise undefined. */
export function parseJsonText(text: string): object | undefined {
  const trimmed = text.trim();
  if (!/^[[{]/.test(trimmed)) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return typeof parsed === "object" && parsed !== null ? parsed : undefined;
  } catch {
    return undefined;
  }
}

/**
 * A JSON object or array, including one the feed cut off mid-value. The preview is only the
 * first two thousand characters of the record, so a page snapshot usually stops inside a string.
 */
export function recoverJson(text: string): object | undefined {
  const trimmed = text.trim();
  if (!/^[[{]/.test(trimmed)) {
    return undefined;
  }
  const whole = parseJsonText(trimmed);
  if (whole) {
    return whole;
  }
  let attempt = trimmed;
  for (let step = 0; step < 12; step++) {
    const parsed = parseJsonText(closeJson(attempt));
    if (parsed) {
      return parsed;
    }
    if (attempt.length < 2) {
      return undefined;
    }
    attempt = attempt.slice(0, -1).trimEnd();
  }
  return undefined;
}

/** Indented JSON. A string that itself has lines is broken at those lines, not left as `\n`. */
export function presentJson(value: unknown): string {
  return presentValue(value, 0);
}

/** Close strings and brackets left open when a JSON text was cut, and fill a dangling colon. */
function closeJson(text: string): string {
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  for (const char of text) {
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }
    if (char === '"') {
      inString = true;
    } else if (char === "{") {
      stack.push("}");
    } else if (char === "[") {
      stack.push("]");
    } else if (
      (char === "}" || char === "]") &&
      stack.at(-1) === (char === "}" ? "}" : "]")
    ) {
      stack.pop();
    }
  }
  let out = escaped ? text.slice(0, -1) : text;
  if (inString) {
    out += '"';
  }
  out = out.replace(/,\s*$/, "");
  if (/:\s*$/.test(out)) {
    out += "null";
  }
  let closers = "";
  while (stack.length > 0) {
    closers += stack.pop();
  }
  return out + closers;
}

function presentValue(value: unknown, depth: number): string {
  const pad = "  ".repeat(depth);
  const inner = "  ".repeat(depth + 1);
  if (typeof value === "string") {
    if (!value.includes("\n")) {
      return JSON.stringify(value);
    }
    const lines = value.split("\n");
    const [first, ...rest] = lines;
    return `"${first ?? ""}\n${rest.map((line) => `${inner}${line}`).join("\n")}"`;
  }
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null
  ) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "[]";
    }
    return `[\n${value.map((item) => `${inner}${presentValue(item, depth + 1)}`).join(",\n")}\n${pad}]`;
  }
  if (!isRecord(value)) {
    return "null";
  }
  const entries = Object.entries(value);
  if (entries.length === 0) {
    return "{}";
  }
  return `{\n${entries
    .map(
      ([key, item]) =>
        `${inner}${JSON.stringify(key)}: ${presentValue(item, depth + 1)}`,
    )
    .join(",\n")}\n${pad}}`;
}

/**
 * The text to show for a value and the language to colour it with. A string is shown exactly
 * as it is, unless it is itself JSON, which is re-indented; anything else becomes JSON.
 */
export function toCode(
  value: unknown,
  language?: string,
): { code: string; language: string | undefined } {
  if (typeof value === "string") {
    const parsed = language ? undefined : parseJsonText(value);
    return parsed === undefined
      ? { code: value, language }
      : { code: JSON.stringify(parsed, null, 2), language: "json" };
  }
  return {
    code: value === undefined ? "" : JSON.stringify(value, null, 2),
    language: "json",
  };
}

export function humanize(key: string): string {
  const words = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim()
    .toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A string field of a JSON object, if it is one. */
export function stringField(value: unknown, key: string): string | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const field = value[key];
  return typeof field === "string" ? field : undefined;
}
