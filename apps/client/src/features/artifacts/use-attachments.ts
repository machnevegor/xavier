import { useSyncExternalStore } from "react";
import { explain, isAbort } from "@/api/errors.ts";
import type { Artifact, MessageInput } from "@/api/types.ts";
import { MAX_UPLOAD_BYTES, uploadArtifact } from "@/features/artifacts/api.ts";
import {
  isImage,
  isInlineImageType,
} from "@/features/artifacts/image-preview.tsx";
import { bytes } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** The tool groups with a workspace, where the backend delivers attached files. */
const WORKSPACE_GROUPS = new Set(["execute", "compute"]);

interface Base {
  key: string;
  file: File;
  /** A local URL of a picked image, for its thumbnail before and after the upload. */
  preview: string | null;
}

export type Attachment =
  | (Base & { state: "uploading"; progress: number })
  | (Base & { state: "failed"; reason: string; retryable: boolean })
  | (Base & { state: "ready"; artifact: Artifact });

const NONE: Attachment[] = [];
const drafts = new Map<string, Attachment[]>();
const listeners = new Set<() => void>();
const aborts = new Map<string, () => void>();
let sequence = 0;

function change(scope: string, next: (items: Attachment[]) => Attachment[]) {
  drafts.set(scope, next(drafts.get(scope) ?? NONE));
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function discard(item: Attachment) {
  aborts.get(item.key)?.();
  aborts.delete(item.key);
  if (item.preview) {
    URL.revokeObjectURL(item.preview);
  }
}

function problem(file: File): string | null {
  if (file.size === 0) {
    return m.attach_the_file_is_empty();
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return m.attach_exceeds({ max: bytes(MAX_UPLOAD_BYTES) });
  }
  return null;
}

/**
 * Files are uploaded as soon as they are picked, so sending only references them. The picks
 * live outside the component, per scope, so they survive leaving the chat for its files or
 * screen and coming back. Raster images go to the model as images when it can see them;
 * everything else is a file the backend puts in the agent's workspace.
 */
export function useAttachments(scope: string, chatId?: string) {
  const items = useSyncExternalStore(
    subscribe,
    () => drafts.get(scope) ?? NONE,
    () => NONE,
  );

  const update = (key: string, next: (item: Attachment) => Attachment) =>
    change(scope, (current) =>
      current.map((item) => (item.key === key ? next(item) : item)),
    );

  const upload = (key: string, file: File) => {
    const { abort, done } = uploadArtifact(file, chatId, (progress) =>
      update(key, (item) =>
        item.state === "uploading" ? { ...item, progress } : item,
      ),
    );
    aborts.set(key, abort);
    done.then(
      (artifact) => {
        aborts.delete(key);
        update(key, (item) => ({ ...item, artifact, state: "ready" }));
      },
      (error: unknown) => {
        aborts.delete(key);
        if (!isAbort(error)) {
          update(key, (item) => ({
            ...item,
            reason: explain(error).title,
            retryable: true,
            state: "failed",
          }));
        }
      },
    );
  };

  const add = (files: Iterable<File>) => {
    for (const file of files) {
      sequence += 1;
      const key = `${scope}-${sequence}`;
      const reason = problem(file);
      const preview = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : null;
      change(scope, (current) => [
        ...current,
        reason
          ? { file, key, preview, reason, retryable: false, state: "failed" }
          : { file, key, preview, progress: 0, state: "uploading" },
      ]);
      if (!reason) {
        upload(key, file);
      }
    }
  };

  const retry = (key: string) => {
    const item = items.find((candidate) => candidate.key === key);
    if (item?.state === "failed" && item.retryable) {
      update(key, (current) => ({
        ...current,
        progress: 0,
        state: "uploading",
      }));
      upload(key, item.file);
    }
  };

  const remove = (key: string) =>
    change(scope, (current) =>
      current.filter((item) => {
        if (item.key === key) {
          discard(item);
        }
        return item.key !== key;
      }),
    );

  /** Forgets the picks after a send; the uploaded files stay stored. */
  const clear = () =>
    change(scope, (current) => {
      for (const item of current) {
        discard(item);
      }
      return NONE;
    });

  const references = (
    seesImages: boolean,
  ): Pick<MessageInput, "image_artifact_ids" | "file_artifact_ids"> => {
    const ready = items.flatMap((item) =>
      item.state === "ready" ? [item.artifact] : [],
    );
    const asImage = (artifact: Artifact) => seesImages && isImage(artifact);
    return {
      file_artifact_ids: ready.filter((a) => !asImage(a)).map((a) => a.id),
      image_artifact_ids: ready.filter(asImage).map((a) => a.id),
    };
  };

  return {
    add,
    clear,
    /** What the agent will not get as the operator may expect, given the chat's settings. */
    note: (seesImages: boolean, toolGroups: readonly string[]) => {
      const kept = items.filter((item) => item.state !== "failed");
      const image = (item: Attachment) => isInlineImageType(item.file.type);
      if (
        kept.some((item) => !(seesImages && image(item))) &&
        !toolGroups.some((group) => WORKSPACE_GROUPS.has(group))
      ) {
        return m.attach_the_agent_can_open_attached_files_only_with();
      }
      if (!seesImages && kept.some(image)) {
        return m.attach_this_model_cannot_see_images_so_they_are();
      }
      return null;
    },
    items,
    ready: items.filter((item) => item.state === "ready").length,
    references,
    remove,
    retry,
    uploading: items.some((item) => item.state === "uploading"),
  };
}
