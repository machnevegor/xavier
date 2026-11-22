import { describe, expect, it } from "vitest";
import type { ChatEvent } from "@/api/types.ts";
import { isNotable, newSince } from "@/features/chats/feed/notable.ts";

const event = (id: string, kind: string, status?: string): ChatEvent => ({
  chat_id: "chat",
  created_at: "2026-11-20T10:00:00Z",
  data: status === undefined ? {} : { status },
  id,
  kind,
  turn_id: "turn",
});

describe("isNotable", () => {
  it.each([
    ["question", "pending", true],
    ["question", "answered", false],
    ["action", "awaiting_approval", true],
    ["action", "suspended", true],
    ["action", "running", false],
    ["turn", "completed", true],
    ["turn", "failed", true],
    ["turn", "running", false],
    ["turn", "interrupted", false],
    ["assistant", undefined, false],
  ])("%s %s is notable: %s", (kind, status, notable) => {
    expect(isNotable(event("1", kind, status))).toBe(notable);
  });
});

describe("newSince", () => {
  const log = [event("a", "turn"), event("b", "turn"), event("c", "turn")];

  it("returns the events after the last one seen", () => {
    expect(newSince(log, "a").map((e) => e.id)).toEqual(["b", "c"]);
  });

  it("returns every event after an empty log", () => {
    expect(newSince(log, null)).toEqual(log);
  });

  it("returns nothing on the first look or after a reload", () => {
    expect(newSince(log, undefined)).toEqual([]);
    expect(newSince(log, "gone")).toEqual([]);
  });
});
