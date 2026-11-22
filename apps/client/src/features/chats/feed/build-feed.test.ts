import { describe, expect, it } from "vitest";
import type { ChatEvent, Turn } from "@/api/types.ts";
import { buildFeed } from "@/features/chats/feed/build-feed.ts";

const turn = (changes: Partial<Turn>): Turn =>
  ({
    chat_id: "chat",
    final_response: null,
    finished_at: null,
    id: "turn",
    started_at: "2026-11-20T10:00:00Z",
    status: "running",
    ...changes,
  }) as Turn;

const said = (text: string, at: string): ChatEvent => ({
  chat_id: "chat",
  created_at: at,
  data: { text },
  id: at,
  kind: "assistant",
  turn_id: "turn",
});

const answers = (turns: Turn[], events: ChatEvent[]) =>
  buildFeed([], turns, [], [], events).filter((item) => item.type === "answer");

describe("buildFeed", () => {
  it("shows a running turn's answer once a step has text", () => {
    const events = [
      said("Looking at the logs", "2026-11-20T10:00:05Z"),
      said("The logs show a timeout", "2026-11-20T10:00:09Z"),
    ];

    expect(answers([turn({})], events)).toEqual([
      expect.objectContaining({
        at: "2026-11-20T10:00:09Z",
        key: "turn-answer",
      }),
    ]);
  });

  it("shows no answer for a running turn that has not said anything", () => {
    expect(answers([turn({})], [])).toEqual([]);
  });

  it("keeps the partial reply of an interrupted turn", () => {
    const interrupted = turn({
      finished_at: "2026-11-20T10:00:30Z",
      status: "interrupted",
    });

    expect(
      answers([interrupted], [said("Half of it", "2026-11-20T10:00:29Z")]),
    ).toEqual([expect.objectContaining({ at: "2026-11-20T10:00:30Z" })]);
  });

  it("places a finished turn's answer at its end", () => {
    const finished = turn({
      final_response: "Done",
      finished_at: "2026-11-20T10:01:00Z",
      status: "completed",
    });

    expect(answers([finished], [said("Done", "2026-11-20T10:00:59Z")])).toEqual(
      [expect.objectContaining({ at: "2026-11-20T10:01:00Z" })],
    );
  });
});
