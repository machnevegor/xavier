import { cn } from "cn";
import { useMemo, useState } from "react";
import { CopyButton } from "@/components/copy-button.tsx";
import { highlight, languageLabel } from "@/lib/highlight.ts";
import { toCode } from "@/lib/json.ts";
import { m } from "@/paraglide/messages.js";

/**
 * Verbatim machine text on the grey fill: real line breaks, sideways scrolling instead of
 * wrapping, long blocks open on a preview. Copy always takes the whole original.
 */
export function CodeBlock({
  code,
  copy,
  language,
  label,
  previewLines = 14,
  bare = false,
  className,
}: {
  code: string;
  copy?: string;
  language?: string | undefined;
  label?: string;
  previewLines?: number;
  /** Inside a frame that already names the block and copies it. */
  bare?: boolean;
  className?: string;
}) {
  const text = code.replace(/\n+$/, "");
  const html = useMemo(() => highlight(text, language), [text, language]);
  const [open, setOpen] = useState(false);
  const lines = text.split("\n").length;
  const long = lines > previewLines + 3;
  const clipped = long && !open;
  const caption = label ?? (language ? languageLabel(language) : undefined);
  const shownCaption = caption ?? m.data_text();
  const head = !bare && (Boolean(caption) || lines > 3);
  const numbered = html !== null && lines >= 8;
  const copyButton = (
    <CopyButton
      label={m.data_copy({ caption: shownCaption })}
      value={copy ?? code}
    />
  );

  return (
    <div
      className={cn(
        "bg-muted max-w-full min-w-0 overflow-hidden rounded-md",
        className,
      )}
    >
      {head && (
        <div className="text-muted-foreground flex h-8 items-center justify-between gap-2 pr-1 pl-3 text-xs">
          <span className="truncate">
            {shownCaption}
            {lines > 3 && ` ${m.data_lines({ count: lines })}`}
          </span>
          {copyButton}
        </div>
      )}
      <section
        aria-label={shownCaption}
        className={cn(
          "flex overflow-x-auto font-mono text-xs leading-5",
          clipped && "overflow-y-hidden",
          head ? "pb-2.5" : bare ? "py-2.5" : "items-center py-1",
        )}
        style={clipped ? { maxHeight: previewLines * 20 } : undefined}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard
        tabIndex={0}
      >
        {numbered && (
          <span
            aria-hidden="true"
            className="bg-muted text-muted-foreground/70 sticky left-0 pr-3 pl-3 text-right whitespace-pre select-none"
          >
            {Array.from({ length: lines }, (_, index) => index + 1).join("\n")}
          </span>
        )}
        <pre className={cn("flex-1 pr-3 whitespace-pre", !numbered && "pl-3")}>
          {html === null ? (
            <code className="whitespace-pre">{text}</code>
          ) : (
            <code
              className="whitespace-pre"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          )}
        </pre>
        {!head && !bare && (
          <span className="bg-muted sticky right-1 self-start">
            {copyButton}
          </span>
        )}
      </section>
      {long && (
        <button
          aria-expanded={open}
          className="text-muted-foreground hover:text-foreground flex h-8 w-full items-center justify-center border-t text-xs"
          onClick={() => setOpen(!open)}
          type="button"
        >
          {open ? m.data_show_less() : m.data_show_all_lines({ n: lines })}
        </button>
      )}
    </div>
  );
}

/** Any value as a code block: text as it is, everything else as indented JSON. */
export function JsonBlock({
  value,
  language,
  label,
  className,
}: {
  value: unknown;
  language?: string;
  label?: string;
  className?: string;
}) {
  const shown = toCode(value, language);
  return (
    <CodeBlock
      className={className}
      code={shown.code || m.common_text()}
      copy={typeof value === "string" ? value : shown.code}
      label={label}
      language={shown.language}
    />
  );
}
