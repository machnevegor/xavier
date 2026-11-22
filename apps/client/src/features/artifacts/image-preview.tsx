import { IconDownload } from "@tabler/icons-react";
import { cn } from "cn";
import type { ReactElement } from "react";
import type { Artifact } from "@/api/types.ts";
import { Button } from "@/components/ui/button.tsx";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog.tsx";
import { artifactUrl } from "@/features/artifacts/api.ts";
import { bytes } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** The raster images the content endpoint serves inline; anything else is only downloaded. */
const INLINE = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

export const isInlineImageType = (contentType: string): boolean =>
  INLINE.has(contentType);

export const isImage = (artifact: Artifact): boolean =>
  isInlineImageType(artifact.content_type);

/** An image at a comfortable size, with the original one click away. */
export function MediaPreview({
  src,
  name,
  detail,
  trigger,
}: {
  src: string;
  name: string;
  detail?: string;
  trigger: ReactElement;
}) {
  return (
    <Dialog>
      <DialogTrigger render={trigger} />
      <DialogContent className="max-h-[calc(100dvh-2rem)] gap-3 p-3 sm:max-w-5xl">
        <div className="flex min-w-0 items-center gap-3 pr-9">
          <DialogTitle className="min-w-0 flex-1 truncate font-mono text-xs leading-normal">
            {name}
          </DialogTitle>
          {detail && (
            <span className="text-muted-foreground shrink-0 text-xs">
              {detail}
            </span>
          )}
          <Button
            aria-label={m.artifact_download({ name })}
            nativeButton={false}
            render={<a download={name} href={src} />}
            size="icon-sm"
            variant="ghost"
          >
            <IconDownload />
          </Button>
        </div>
        <img
          alt={name}
          className="bg-muted mx-auto max-h-[calc(100dvh-7rem)] max-w-full rounded-md object-contain"
          src={src}
        />
      </DialogContent>
    </Dialog>
  );
}

export function ImagePreview({
  artifact,
  trigger,
}: {
  artifact: Artifact;
  trigger: ReactElement;
}) {
  return (
    <MediaPreview
      detail={bytes(artifact.size_bytes)}
      name={artifact.name}
      src={artifactUrl(artifact.id)}
      trigger={trigger}
    />
  );
}

/** A stored image shown in place; it opens larger. */
export function ImageThumbnail({
  artifact,
  className,
}: {
  artifact: Artifact;
  className?: string;
}) {
  return (
    <ImagePreview
      artifact={artifact}
      trigger={
        <button
          aria-label={m.artifact_open({ name: artifact.name })}
          className="focus-visible:ring-ring/50 block overflow-hidden rounded-md border outline-none focus-visible:ring-3"
          type="button"
        >
          <img
            alt=""
            className={cn("bg-muted block object-cover", className)}
            loading="lazy"
            src={artifactUrl(artifact.id)}
          />
        </button>
      }
    />
  );
}
