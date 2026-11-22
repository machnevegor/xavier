import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { m } from "@/paraglide/messages.js";

const LABELS = {
  bash: "Bash",
  css: "CSS",
  diff: "Diff",
  javascript: "JavaScript",
  json: "JSON",
  markdown: "Markdown",
  python: "Python",
  sql: "SQL",
  typescript: "TypeScript",
  xml: "HTML",
  yaml: "YAML",
} as const;

type Language = keyof typeof LABELS;

for (const [name, definition] of Object.entries({
  bash,
  css,
  diff,
  javascript,
  json,
  markdown,
  python,
  sql,
  typescript,
  xml,
  yaml,
})) {
  hljs.registerLanguage(name, definition);
}

const ALIASES: Record<string, Language> = {
  cjs: "javascript",
  console: "bash",
  html: "xml",
  js: "javascript",
  jsonc: "json",
  jsx: "javascript",
  md: "markdown",
  mjs: "javascript",
  node: "javascript",
  nodejs: "javascript",
  patch: "diff",
  py: "python",
  python3: "python",
  sh: "bash",
  shell: "bash",
  svg: "xml",
  ts: "typescript",
  tsx: "typescript",
  yml: "yaml",
  zsh: "bash",
};

const MAX_HIGHLIGHT = 200_000;

const isLanguage = (key: string): key is Language => key in LABELS;

/** The language this app can colour, or null. Nothing is guessed from the text itself. */
export function languageId(name: string | null | undefined): Language | null {
  const key = (name ?? "").trim().toLowerCase();
  const id = ALIASES[key] ?? key;
  return isLanguage(id) ? id : null;
}

export function languageLabel(name: string | null | undefined): string {
  const id = languageId(name);
  return id ? LABELS[id] : name || m.data_text();
}

/** Escaped HTML with token spans, or null when the text should be shown plain. */
export function highlight(
  code: string,
  name: string | null | undefined,
): string | null {
  const id = languageId(name);
  if (!id || code.length > MAX_HIGHLIGHT) {
    return null;
  }
  return hljs.highlight(code, { ignoreIllegals: true, language: id }).value;
}
