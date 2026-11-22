import { ApiError, apiError, isAbort, networkError } from "@/api/errors.ts";
import { type ChatEvent, isRecord } from "@/api/types.ts";

export type StreamState = "connecting" | "live" | "reconnecting" | "failed";

const MAX_DELAY_MS = 15_000;

interface Frame {
  id: string | null;
  data: string;
}

/** Split a server-sent event stream into frames; comments and keep-alives yield nothing. */
export async function* readFrames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<Frame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let id: string | null = null;
  let data: string[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r\n|\r|\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line === "") {
        if (data.length > 0) {
          yield { data: data.join("\n"), id };
        }
        id = null;
        data = [];
      } else if (line.startsWith("data:")) {
        data.push(line.slice(5).replace(/^ /, ""));
      } else if (line.startsWith("id:")) {
        id = line.slice(3).trim();
      }
    }
  }
}

/** Events cross an untrusted boundary, so the envelope is checked; `data` stays opaque. */
export function isChatEvent(value: unknown): value is ChatEvent {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.chat_id === "string" &&
    typeof value.kind === "string" &&
    typeof value.created_at === "string" &&
    (value.turn_id === null || typeof value.turn_id === "string")
  );
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

interface StreamOptions {
  cursor: string | null;
  onEvent: (event: ChatEvent) => void;
  onState: (state: StreamState) => void;
}

/**
 * Follow a chat's event stream, resuming after the last delivered event when the connection
 * drops. Retries back off up to 15 s; 401 and 404 end the stream as `failed`.
 *
 * Returns a function that closes the stream.
 */
export function openChatStream(
  chatId: string,
  { cursor, onEvent, onState }: StreamOptions,
): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  let last = cursor;
  let attempt = 0;

  async function connect(): Promise<void> {
    const url = new URL(
      `/api/v1/chats/${encodeURIComponent(chatId)}/events/stream`,
      window.location.origin,
    );
    if (last) {
      url.searchParams.set("cursor", last);
    }
    let response: Response;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "text/event-stream" },
        signal,
      });
    } catch (cause) {
      throw isAbort(cause) ? cause : networkError(cause);
    }
    if (!response.ok || !response.body) {
      throw apiError(response, await response.json().catch(() => null));
    }
    onState("live");
    attempt = 0;
    for await (const frame of readFrames(response.body)) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(frame.data);
      } catch {
        continue;
      }
      if (isChatEvent(parsed)) {
        last = frame.id ?? parsed.id;
        onEvent(parsed);
      }
    }
  }

  void (async () => {
    while (!signal.aborted) {
      onState(attempt === 0 ? "connecting" : "reconnecting");
      try {
        await connect();
      } catch (error) {
        if (signal.aborted) {
          return;
        }
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.status === 404)
        ) {
          onState("failed");
          return;
        }
      }
      if (signal.aborted) {
        return;
      }
      attempt += 1;
      onState("reconnecting");
      await wait(Math.min(1000 * 2 ** (attempt - 1), MAX_DELAY_MS), signal);
    }
  })();

  return () => controller.abort();
}
