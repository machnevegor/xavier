import type { ChatEvent } from "@/api/types.ts";
import { isRecord } from "@/api/types.ts";

const FINISHED = new Set(["completed", "failed", "limited", "stopped"]);

/**
 * Whether an event is worth a nudge to a reader who has scrolled away: the agent asks a
 * question or waits for a decision, or a turn has finished.
 */
export function isNotable(event: ChatEvent): boolean {
  const status = isRecord(event.data) ? event.data.status : undefined;
  switch (event.kind) {
    case "question":
      return status === "pending";
    case "action":
      return status === "awaiting_approval" || status === "suspended";
    case "turn":
      return typeof status === "string" && FINISHED.has(status);
    default:
      return false;
  }
}

/**
 * The events after `seen`, the id of the last event already looked at, or `null` when the log
 * was empty then. `undefined` (the first look) or an unknown id (a reloaded log) yields
 * nothing, so old events never count as new.
 */
export function newSince(
  events: readonly ChatEvent[],
  seen: string | null | undefined,
): readonly ChatEvent[] {
  if (seen === null) {
    return events;
  }
  const index =
    seen === undefined ? -1 : events.findIndex((event) => event.id === seen);
  return index === -1 ? [] : events.slice(index + 1);
}
