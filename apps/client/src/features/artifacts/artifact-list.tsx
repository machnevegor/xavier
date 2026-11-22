import { IconDownload, IconFile } from "@tabler/icons-react";
import { useInfiniteQuery, useQueries } from "@tanstack/react-query";
import { cn } from "cn";
import type { ReactNode } from "react";
import { flatten } from "@/api/query.ts";
import { ErrorState, LoadingRows, LoadMore } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Skeleton } from "@/components/ui/skeleton.tsx";
import { artifactQueries, artifactUrl } from "@/features/artifacts/api.ts";
import { DataPreview, dataKind } from "@/features/artifacts/data-preview.tsx";
import {
  ImageThumbnail,
  isImage,
} from "@/features/artifacts/image-preview.tsx";
import { bytes, shortId, timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/**
 * Stored files by id, as a tool call or a code run names them. Images show in place and open
 * larger; other files download directly, named once their metadata has loaded.
 */
export function ArtifactChips({
  ids,
  imageClassName = "h-24 max-w-48",
  className,
}: {
  ids: string[];
  imageClassName?: string;
  className?: string;
}) {
  const details = useQueries({
    queries: ids.map((id) => artifactQueries.detail(id)),
  });
  return (
    <ul
      aria-label={m.artifact_stored_files()}
      className={cn("flex flex-wrap items-start gap-2", className)}
    >
      {ids.map((id, index) => {
        const detail = details[index];
        const artifact = detail?.data;
        if (detail?.isPending) {
          return (
            <li key={id}>
              <Skeleton className="h-9 w-40" />
            </li>
          );
        }
        const kind = artifact ? dataKind(artifact) : null;
        return (
          <li key={id}>
            {artifact && isImage(artifact) ? (
              <ImageThumbnail artifact={artifact} className={imageClassName} />
            ) : (
              <span className="bg-card inline-flex max-w-full items-center gap-1 rounded-md border py-0.5 pr-0.5 pl-2.5">
                <a
                  className="hover:bg-accent inline-flex min-h-10 min-w-0 items-center gap-2 rounded-md px-1 sm:min-h-8"
                  download
                  href={artifactUrl(id)}
                >
                  <IconDownload
                    aria-hidden="true"
                    className="text-muted-foreground size-3.5 shrink-0"
                  />
                  <span className="truncate font-mono text-xs">
                    {artifact?.name ??
                      m.artifact_stored_file({ id: shortId(id) })}
                  </span>
                  {artifact && (
                    <span className="text-muted-foreground shrink-0 text-xs">
                      {bytes(artifact.size_bytes)}
                    </span>
                  )}
                </a>
                {artifact && kind && (
                  <DataPreview artifact={artifact} kind={kind} />
                )}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const SOURCES: Record<string, () => string> = {
  embed: m.artifact_shown_in_a_reply,
  export: m.artifact_exported,
  output: m.artifact_code_output,
  result: m.artifact_tool_result,
  screenshot: m.artifact_screenshot,
  upload: m.artifact_uploaded,
};

/** The chat's delivered files other than images, which the Media tab shows. */
export function ArtifactList({
  chatId,
  empty,
}: {
  chatId: string;
  empty: ReactNode;
}) {
  const query = useInfiniteQuery(artifactQueries.ofChat(chatId, "file"));
  if (query.isPending) {
    return <LoadingRows rows={2} />;
  }
  if (query.isError) {
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }
  const artifacts = flatten(query.data);
  if (artifacts.length === 0 && !query.hasNextPage) {
    return empty;
  }
  return (
    <>
      <ul className="divide-y">
        {artifacts.map((artifact) => {
          const kind = dataKind(artifact);
          return (
            <li className="flex min-h-12 items-center gap-3" key={artifact.id}>
              <IconFile
                aria-hidden="true"
                className="text-muted-foreground size-5 shrink-0"
                stroke={1.5}
              />
              <span className="min-w-0 flex-1 py-1.5">
                <span className="block truncate text-sm">{artifact.name}</span>
                <span className="text-muted-foreground block truncate text-xs">
                  {bytes(artifact.size_bytes)} /{" "}
                  {SOURCES[artifact.source]?.() ?? artifact.source} /{" "}
                  {timeAgo(artifact.created_at)}
                </span>
              </span>
              {kind && <DataPreview artifact={artifact} kind={kind} />}
              <Button
                aria-label={m.artifact_download({ name: artifact.name })}
                nativeButton={false}
                render={<a download href={artifactUrl(artifact.id)} />}
                size="icon-sm"
                variant="ghost"
              >
                <IconDownload />
              </Button>
            </li>
          );
        })}
      </ul>
      <LoadMore {...query} />
    </>
  );
}
