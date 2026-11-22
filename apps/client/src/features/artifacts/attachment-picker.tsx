import {
  IconAlertTriangle,
  IconFile,
  IconPaperclip,
  IconRefresh,
  IconX,
} from "@tabler/icons-react";
import { cn } from "cn";
import {
  type ClipboardEvent,
  type DragEvent,
  type ReactNode,
  useState,
} from "react";
import { Button, buttonVariants } from "@/components/ui/button.tsx";
import type {
  Attachment,
  useAttachments,
} from "@/features/artifacts/use-attachments.ts";
import { bytes } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

type Attachments = ReturnType<typeof useAttachments>;

/**
 * The file input itself, transparent over the icon: phones ignore a scripted click on a
 * hidden input, which left attaching silently doing nothing there.
 */
export function AttachButton({ attachments }: { attachments: Attachments }) {
  return (
    <label
      className={cn(
        buttonVariants({ size: "icon", variant: "ghost" }),
        "text-muted-foreground has-focus-visible:ring-ring/50 relative shrink-0 cursor-pointer has-focus-visible:ring-3",
      )}
      title={m.attach_attach_files()}
    >
      <IconPaperclip aria-hidden="true" />
      <input
        aria-label={m.attach_attach_files()}
        className="absolute inset-0 cursor-pointer opacity-0"
        multiple
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          attachments.add(files);
        }}
        type="file"
      />
    </label>
  );
}

/** Pasted files are attached; a paste that carries text as well stays a text paste. */
export function pasteFiles(
  event: ClipboardEvent<HTMLElement>,
  attachments: Attachments,
) {
  const files = Array.from(event.clipboardData.files);
  if (files.length > 0 && !event.clipboardData.getData("text/plain")) {
    event.preventDefault();
    attachments.add(files);
  }
}

/** The message box, which also takes files dragged onto it. */
export function FileDropZone({
  attachments,
  className,
  children,
}: {
  attachments: Attachments;
  className?: string;
  children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const carriesFiles = (event: DragEvent) =>
    event.dataTransfer.types.includes("Files");
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: dropping is a pointer shortcut; the attach button is the accessible way
    <div
      className={cn(
        "border-input bg-card focus-within:border-primary rounded-lg border",
        over && "border-primary border-dashed",
        className,
      )}
      onDragLeave={() => setOver(false)}
      onDragOver={(event) => {
        if (carriesFiles(event)) {
          event.preventDefault();
          setOver(true);
        }
      }}
      onDrop={(event) => {
        if (carriesFiles(event)) {
          event.preventDefault();
          setOver(false);
          attachments.add(Array.from(event.dataTransfer.files));
        }
      }}
    >
      {children}
    </div>
  );
}

function stateText(item: Attachment): string {
  switch (item.state) {
    case "uploading":
      return m.attach_uploading({
        percent: Math.round(item.progress * 100),
      });
    case "failed":
      return item.reason;
    case "ready":
      return bytes(item.file.size);
  }
}

function Progress({ item }: { item: Attachment }) {
  if (item.state !== "uploading") {
    return null;
  }
  return (
    <span
      aria-hidden="true"
      className="bg-primary absolute bottom-0 left-0 h-0.5 transition-[width]"
      style={{ width: `${Math.max(4, item.progress * 100)}%` }}
    />
  );
}

function Controls({
  item,
  attachments,
}: {
  item: Attachment;
  attachments: Attachments;
}) {
  return (
    <span className="flex shrink-0">
      {item.state === "failed" && item.retryable && (
        <Button
          aria-label={m.attach_retry({ name: item.file.name })}
          className="size-6"
          onClick={() => attachments.retry(item.key)}
          size="icon"
          type="button"
          variant="ghost"
        >
          <IconRefresh />
        </Button>
      )}
      <Button
        aria-label={m.attach_remove({ name: item.file.name })}
        className="size-6"
        onClick={() => attachments.remove(item.key)}
        size="icon"
        type="button"
        variant="ghost"
      >
        <IconX />
      </Button>
    </span>
  );
}

/**
 * The picked files while a message is written: images as thumbnails, other files as chips,
 * each with its upload progress or the reason it failed, and a way to drop or retry it.
 */
export function AttachmentList({
  attachments,
  className,
}: {
  attachments: Attachments;
  className?: string;
}) {
  if (attachments.items.length === 0) {
    return null;
  }
  return (
    <ul
      aria-label={m.attach_attachments()}
      className={cn("flex flex-wrap gap-2", className)}
    >
      {attachments.items.map((item) => {
        const failed = item.state === "failed";
        const status = stateText(item);
        return (
          <li
            aria-busy={item.state === "uploading"}
            className={cn(
              "bg-muted/40 relative flex max-w-full items-center gap-2 overflow-hidden rounded-md border text-xs",
              failed && "border-destructive/60",
              item.preview ? "p-1 pr-0.5" : "py-1 pr-0.5 pl-2",
            )}
            key={item.key}
            title={`${item.file.name} — ${status}`}
          >
            {item.preview ? (
              <img
                alt=""
                className={cn(
                  "size-10 shrink-0 rounded object-cover",
                  item.state === "uploading" && "opacity-60",
                )}
                src={item.preview}
              />
            ) : failed ? (
              <IconAlertTriangle
                aria-hidden="true"
                className="text-destructive size-4 shrink-0"
              />
            ) : (
              <IconFile
                aria-hidden="true"
                className="text-muted-foreground size-4 shrink-0"
              />
            )}
            <span className="flex max-w-44 min-w-0 flex-col leading-tight">
              <span className="truncate">{item.file.name}</span>
              <span
                className={cn(
                  "truncate",
                  failed ? "text-destructive" : "text-muted-foreground",
                )}
                role={failed ? "alert" : undefined}
              >
                {status}
              </span>
            </span>
            <Controls attachments={attachments} item={item} />
            <Progress item={item} />
          </li>
        );
      })}
    </ul>
  );
}
