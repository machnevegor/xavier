import { IconEye } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import type { Artifact } from "@/api/types.ts";
import { CodeBlock } from "@/components/code-block.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog.tsx";
import { CsvTable } from "@/components/viz/csv-view.tsx";
import { artifactUrl } from "@/features/artifacts/api.ts";
import { parseCsv } from "@/lib/csv.ts";
import { bytes } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** Larger files are only downloaded: reading them in the page would stall it. */
const MAX_PREVIEW_BYTES = 2 * 1024 * 1024;

export type DataKind = "csv" | "json";

/** A stored file that opens as a table or highlighted text, judged by its type or name. */
export function dataKind(artifact: Artifact): DataKind | null {
  const type = artifact.content_type.split(";")[0]?.trim();
  const name = artifact.name.toLowerCase();
  if (
    type === "text/csv" ||
    type === "text/tab-separated-values" ||
    /\.[ct]sv$/.test(name)
  ) {
    return "csv";
  }
  if (type === "application/json" || name.endsWith(".json")) {
    return "json";
  }
  return null;
}

async function readText(artifactId: string, signal: AbortSignal) {
  const response = await fetch(artifactUrl(artifactId), { signal });
  if (!response.ok) {
    throw new Error(
      m.artifact_the_file_could_not_be_read_http({ status: response.status }),
    );
  }
  return response.text();
}

function Contents({ text, kind }: { text: string; kind: DataKind }) {
  const table = useMemo(
    () => (kind === "csv" ? parseCsv(text) : null),
    [text, kind],
  );
  if (table && table.rows.length > 0) {
    return <CsvTable expanded table={table} />;
  }
  return <CodeBlock code={text} language={kind} />;
}

function Body({ artifact, kind }: { artifact: Artifact; kind: DataKind }) {
  const text = useQuery({
    queryFn: ({ signal }) => readText(artifact.id, signal),
    queryKey: ["artifact", artifact.id, "text"],
    staleTime: Number.POSITIVE_INFINITY,
  });
  if (text.isPending) {
    return <LoadingRows rows={4} />;
  }
  if (text.isError) {
    return (
      <ErrorState error={text.error} onRetry={() => void text.refetch()} />
    );
  }
  return <Contents kind={kind} text={text.data} />;
}

/** A stored CSV or JSON file opened as a table or highlighted text, without leaving the page. */
export function DataPreview({
  artifact,
  kind,
}: {
  artifact: Artifact;
  kind: DataKind;
}) {
  const tooLarge = artifact.size_bytes > MAX_PREVIEW_BYTES;
  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button
            aria-label={m.artifact_preview({ name: artifact.name })}
            disabled={tooLarge}
            size="icon"
            title={
              tooLarge
                ? m.artifact_larger_than_download_it_to_open({
                    max: bytes(MAX_PREVIEW_BYTES),
                  })
                : undefined
            }
            variant="ghost"
          />
        }
      >
        <IconEye />
      </DialogTrigger>
      <DialogContent className="flex h-[calc(100dvh-2rem)] max-h-[calc(100dvh-2rem)] flex-col gap-3 overflow-hidden p-3 sm:max-w-[min(96vw,72rem)]">
        <DialogTitle className="pr-10 font-mono text-xs leading-8">
          {artifact.name}
        </DialogTitle>
        <div className="min-h-0 flex-1 overflow-auto">
          <Body artifact={artifact} kind={kind} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
