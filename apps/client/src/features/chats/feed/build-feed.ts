import type {
  Action,
  ChatEvent,
  Message,
  Question,
  Turn,
  TurnStatus,
} from "@/api/types.ts";
import { isWaiting } from "@/features/actions/model.ts";
import { assistantEvent, textOf } from "@/features/chats/feed/answer-media.ts";
import { m } from "@/paraglide/messages.js";

export type FeedItem =
  | { key: string; at: string; type: "message"; value: Message }
  | {
      key: string;
      at: string;
      type: "activity";
      turn: Turn | null;
      actions: Action[];
    }
  | { key: string; at: string; type: "approval"; value: Action }
  | { key: string; at: string; type: "question"; value: Question }
  | { key: string; at: string; type: "answer"; value: Turn }
  | { key: string; at: string; type: "outcome"; value: Turn };

export const OUTCOME: Partial<Record<TurnStatus, (turn: Turn) => string>> = {
  failed: (turn) =>
    turn.reason
      ? m.chat_failed_send_a_message_to_try_again({ reason: turn.reason })
      : m.chat_failed_send_a_message_to_try_again_2(),
  interrupted: () =>
    m.chat_interrupted_by_your_new_message_finished_steps_are(),
  limited: (turn) =>
    turn.reason
      ? m.chat_stopped_at_a_limit({ reason: turn.reason })
      : m.chat_stopped_at_a_limit_2(),
  stopped: () => m.chat_stopped_before_it_finished(),
};

/**
 * One ordered conversation from the typed resources. A turn's tool calls are one entry, not
 * one each; a call that waits for a decision stands on its own. A turn's answer is its final
 * response, or the text of its latest model step while it runs or when it ended early.
 */
export function buildFeed(
  messages: readonly Message[],
  turns: readonly Turn[],
  actions: readonly Action[],
  questions: readonly Question[] = [],
  events: readonly ChatEvent[] = [],
): FeedItem[] {
  const items: FeedItem[] = messages.map((message) => ({
    at: message.created_at,
    key: message.id,
    type: "message",
    value: message,
  }));
  for (const question of questions) {
    items.push({
      at: question.created_at,
      key: `question-${question.id}`,
      type: "question",
      value: question,
    });
  }
  const byTurn = new Map<string, Action[]>();
  for (const action of actions) {
    byTurn.set(action.turn_id, [...(byTurn.get(action.turn_id) ?? []), action]);
  }
  const known = new Map(turns.map((turn) => [turn.id, turn]));

  for (const [turnId, mine] of byTurn) {
    const turn = known.get(turnId) ?? null;
    const first = mine.find((action) => !isWaiting(action));
    if (first && turn?.status !== "running") {
      items.push({
        actions: mine,
        at: first.created_at,
        key: `${turnId}-activity`,
        turn,
        type: "activity",
      });
    }
    for (const action of mine.filter(isWaiting)) {
      items.push({
        at: action.created_at,
        key: action.id,
        type: "approval",
        value: action,
      });
    }
  }
  for (const turn of turns) {
    const end = turn.finished_at ?? turn.started_at;
    if (turn.status === "running") {
      const mine = byTurn.get(turn.id) ?? [];
      items.push({
        actions: mine,
        at: mine[0]?.created_at ?? turn.started_at,
        key: `${turn.id}-activity`,
        turn,
        type: "activity",
      });
    }
    const said = assistantEvent(events, turn.id);
    if (turn.final_response || textOf(said)) {
      items.push({
        at: turn.status === "running" && said ? said.created_at : end,
        key: `${turn.id}-answer`,
        type: "answer",
        value: turn,
      });
    }
    if (OUTCOME[turn.status]) {
      items.push({
        at: end,
        key: `${turn.id}-outcome`,
        type: "outcome",
        value: turn,
      });
    }
  }
  return items.sort(
    (a, b) => a.at.localeCompare(b.at) || a.key.localeCompare(b.key),
  );
}
