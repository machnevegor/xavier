import { IconAlertTriangle, IconMaximize } from "@tabler/icons-react";
import { cn } from "cn";
import { type ReactNode, useState } from "react";
import { CodeBlock } from "@/components/code-block.tsx";
import { CopyButton } from "@/components/copy-button.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog.tsx";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group.tsx";
import { m } from "@/paraglide/messages.js";

type View = "preview" | "code";

/**
 * A fenced block the chat can show as more than text: a diagram, a chart, a table or a tree.
 * It opens on the rendered view; the source is one toggle away, is what Copy takes, and is
 * what a failed render falls back to. The rendered view can also open large.
 */
export function VizBlock({
  label,
  previewLabel = m.data_preview(),
  code,
  language,
  expandable = true,
  children,
}: {
  label: string;
  previewLabel?: string;
  code: string;
  language: string;
  expandable?: boolean;
  /** The rendered view; `expanded` is true inside the large viewer. */
  children: (expanded: boolean) => ReactNode;
}) {
  const [view, setView] = useState<View>("preview");
  return (
    <figure
      aria-label={label}
      className="bg-muted max-w-full min-w-0 overflow-hidden rounded-md"
    >
      <div className="text-muted-foreground flex h-9 items-center gap-1 pr-1 pl-3 text-xs">
        <figcaption className="min-w-0 flex-1 truncate">{label}</figcaption>
        <ToggleGroup
          aria-label={m.data_view()}
          onValueChange={([next]) => {
            if (next === "preview" || next === "code") {
              setView(next);
            }
          }}
          size="sm"
          spacing={0}
          value={[view]}
        >
          {(["preview", "code"] as const).map((option) => (
            <ToggleGroupItem
              className="text-muted-foreground aria-pressed:bg-background aria-pressed:text-foreground h-7 px-2 text-xs"
              key={option}
              value={option}
            >
              {option === "preview" ? previewLabel : m.data_code()}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <CopyButton label={m.data_copy_source()} value={code} />
        {expandable && (
          <Dialog>
            <DialogTrigger
              render={
                <Button
                  aria-label={m.data_expand({ label })}
                  className="text-muted-foreground"
                  size="icon-xs"
                  title={m.data_expand_2()}
                  variant="ghost"
                />
              }
            >
              <IconMaximize />
            </DialogTrigger>
            <DialogContent className="flex h-[calc(100dvh-2rem)] max-h-[calc(100dvh-2rem)] flex-col gap-3 overflow-hidden p-3 sm:max-w-[min(96vw,90rem)]">
              <DialogTitle className="pr-10 text-sm leading-8">
                {label}
              </DialogTitle>
              <div className="min-h-0 flex-1 overflow-auto">
                {children(true)}
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>
      {view === "code" ? (
        <CodeBlock bare code={code} language={language} />
      ) : (
        <div className="bg-background/60 m-1 mt-0 overflow-hidden rounded">
          {children(false)}
        </div>
      )}
    </figure>
  );
}

/** Shown in place of a drawing while its library loads or the block is still arriving. */
export function VizLoading({ className }: { className?: string }) {
  return (
    <div
      aria-busy="true"
      className={cn(
        "text-muted-foreground flex h-48 items-center justify-center text-xs",
        className,
      )}
    >
      <span className="shimmer-text">{m.data_rendering()}</span>
    </div>
  );
}

/** A render that did not work: why, in one quiet line, and the source as it came. */
export function VizFallback({
  reason,
  code,
  language,
}: {
  reason: string;
  code: string;
  language: string;
}) {
  return (
    <div>
      <p className="text-muted-foreground flex items-start gap-1.5 px-3 pt-2.5 text-xs">
        <IconAlertTriangle
          aria-hidden="true"
          className="text-attention mt-px size-3.5 shrink-0"
        />
        <span className="break-words">
          {reason} {m.data_showing_the_source_instead()}
        </span>
      </p>
      <CodeBlock bare code={code} language={language} />
    </div>
  );
}
