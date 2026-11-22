import { IconDownload, IconFile, IconFolder } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  fileUrl,
  WORKSPACE_ROOT,
  workspaceQueries,
} from "@/features/workspace/api.ts";
import { bytes } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export function FileBrowser({
  chatId,
  path,
  onOpen,
}: {
  chatId: string;
  path: string;
  onOpen: (path: string) => void;
}) {
  const files = useQuery(workspaceQueries.files(chatId, path));
  const crumbs = path.slice(WORKSPACE_ROOT.length).split("/").filter(Boolean);
  const entries = [...(files.data?.entries ?? [])].sort(
    (a, b) =>
      (a.kind === "dir" ? 0 : 1) - (b.kind === "dir" ? 0 : 1) ||
      a.name.localeCompare(b.name),
  );

  return (
    <div>
      <nav
        aria-label={m.chat_folder_path()}
        className="flex flex-wrap items-center gap-1 font-mono text-sm"
      >
        <button
          className="rounded px-1 underline-offset-2 hover:underline"
          onClick={() => onOpen(WORKSPACE_ROOT)}
          type="button"
        >
          {WORKSPACE_ROOT}
        </button>
        {crumbs.map((crumb, index) => (
          <span
            className="flex items-center gap-1"
            key={crumbs.slice(0, index + 1).join("/")}
          >
            <span aria-hidden="true">/</span>
            <button
              className="rounded px-1 underline-offset-2 hover:underline"
              onClick={() =>
                onOpen(
                  `${WORKSPACE_ROOT}/${crumbs.slice(0, index + 1).join("/")}`,
                )
              }
              type="button"
            >
              {crumb}
            </button>
          </span>
        ))}
      </nav>
      <div className="mt-3">
        {files.isPending ? (
          <LoadingRows />
        ) : files.isError ? (
          <ErrorState
            error={files.error}
            onRetry={() => void files.refetch()}
          />
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground py-6">
            {m.chat_this_folder_is_empty()}
          </p>
        ) : (
          <ul className="divide-y border-y">
            {entries.map((entry) => {
              const full = `${path}/${entry.name}`;
              return (
                <li
                  className="flex min-h-11 items-center gap-3 py-1"
                  key={entry.name}
                >
                  {entry.kind === "dir" ? (
                    <button
                      className="flex min-w-0 flex-1 items-center gap-3 rounded py-1 text-left hover:underline"
                      onClick={() => onOpen(full)}
                      type="button"
                    >
                      <IconFolder
                        aria-hidden="true"
                        className="text-muted-foreground size-4 shrink-0"
                      />
                      <span className="truncate font-mono text-sm">
                        {entry.name}/
                      </span>
                    </button>
                  ) : (
                    <>
                      <IconFile
                        aria-hidden="true"
                        className="text-muted-foreground size-4 shrink-0"
                      />
                      <span className="min-w-0 flex-1 truncate font-mono text-sm">
                        {entry.name}
                      </span>
                      <span className="text-muted-foreground">
                        {bytes(entry.size)}
                      </span>
                      <Button
                        aria-label={m.chat_download({ name: entry.name })}
                        nativeButton={false}
                        render={<a download href={fileUrl(chatId, full)} />}
                        size="icon"
                        variant="ghost"
                      >
                        <IconDownload />
                      </Button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
