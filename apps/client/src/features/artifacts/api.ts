import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { apiError, networkError } from "@/api/errors.ts";
import { pagesOptions } from "@/api/query.ts";
import type { Artifact } from "@/api/types.ts";
import { m } from "@/paraglide/messages.js";

/** Same-origin, so the session cookie authorises it; the server names the file. */
export const artifactUrl = (artifactId: string) =>
  `/api/v1/artifacts/${encodeURIComponent(artifactId)}/content`;

export const artifactQueries = {
  /** Artifacts never change, so their metadata is read once. */
  detail: (artifactId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/artifacts/{artifact_id}", {
            params: { path: { artifact_id: artifactId } },
            signal,
          }),
        ),
      queryKey: ["artifact", artifactId],
      staleTime: Number.POSITIVE_INFINITY,
    }),
  ofChat: (chatId: string, kind?: "file" | "image") =>
    pagesOptions(
      ["chat", chatId, "artifacts", kind ?? "all"],
      (cursor, signal) =>
        readPage(
          api.GET("/api/v1/artifacts", {
            params: {
              query: {
                cursor,
                "filter[chat_id]": [chatId],
                ...(kind ? { "filter[kind]": kind } : {}),
                per_page: 50,
              },
            },
            signal,
          }),
        ),
    ),
};

/** The largest upload the API accepts (src/storage/artifacts.py). */
export const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;

/**
 * Upload one file. It goes through XMLHttpRequest rather than the typed client because only
 * that reports upload progress; failures become the same `ApiError`s.
 */
export function uploadArtifact(
  file: File,
  chatId: string | undefined,
  onProgress: (fraction: number) => void,
): { done: Promise<Artifact>; abort: () => void } {
  const request = new XMLHttpRequest();
  const form = new FormData();
  form.append("file", file);
  if (chatId) {
    form.append("chat_id", chatId);
  }
  const done = new Promise<Artifact>((resolve, reject) => {
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded / event.total);
      }
    });
    request.addEventListener("load", () => {
      const body: unknown = request.response;
      if (request.status >= 200 && request.status < 300) {
        resolve((body as { data: Artifact }).data);
      } else {
        reject(
          apiError(
            new Response(null, {
              status: request.status,
              statusText: request.statusText,
            }),
            body,
          ),
        );
      }
    });
    request.addEventListener("error", () =>
      reject(networkError(new Error(m.attach_the_upload_was_interrupted()))),
    );
    request.addEventListener("abort", () =>
      reject(new DOMException(m.attach_upload_cancelled(), "AbortError")),
    );
  });
  request.open("POST", "/api/v1/artifacts");
  request.responseType = "json";
  request.send(form);
  return { abort: () => request.abort(), done };
}
