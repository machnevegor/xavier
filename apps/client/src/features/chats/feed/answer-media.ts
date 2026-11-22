import type { ChatEvent } from "@/api/types.ts";
import { isRecord } from "@/api/types.ts";

export type EmbedRef = {
  status: string;
  artifact_id?: string;
  name?: string;
  content_type?: string;
  size_bytes?: number;
};

/** HTTPS image URLs of an assistant event, in order, for the media proxy. */
export function mediaOf(event: ChatEvent | undefined): string[] {
  if (!event || !isRecord(event.data)) {
    return [];
  }
  const media = event.data.media;
  return Array.isArray(media)
    ? media.filter((url): url is string => typeof url === "string")
    : [];
}

/** Where the server proxies the `index`th image of an assistant event. */
export const mediaUrl = (
  chatId: string,
  eventId: string,
  index: number,
): string =>
  `/api/v1/chats/${encodeURIComponent(chatId)}/events/${encodeURIComponent(eventId)}/media/${index}`;

/** Workspace embeds keyed by `file:///workspace/...` reference. */
export function embedsOf(
  event: ChatEvent | undefined,
): Record<string, EmbedRef> {
  if (!event || !isRecord(event.data)) {
    return {};
  }
  const embeds = event.data.embeds;
  if (!isRecord(embeds)) {
    return {};
  }
  const out: Record<string, EmbedRef> = {};
  for (const [ref, value] of Object.entries(embeds)) {
    if (!isRecord(value) || typeof value.status !== "string") {
      continue;
    }
    out[ref] = {
      artifact_id:
        typeof value.artifact_id === "string" ? value.artifact_id : undefined,
      content_type:
        typeof value.content_type === "string" ? value.content_type : undefined,
      name: typeof value.name === "string" ? value.name : undefined,
      size_bytes:
        typeof value.size_bytes === "number" ? value.size_bytes : undefined,
      status: value.status,
    };
  }
  return out;
}

/** The text of an assistant event, or `""`. */
export function textOf(event: ChatEvent | undefined): string {
  return event && isRecord(event.data) && typeof event.data.text === "string"
    ? event.data.text
    : "";
}

/** The latest assistant event of a turn (its text, embeds and media). */
export function assistantEvent(
  events: readonly ChatEvent[],
  turnId: string,
): ChatEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.turn_id === turnId && event.kind === "assistant") {
      return event;
    }
  }
  return undefined;
}
