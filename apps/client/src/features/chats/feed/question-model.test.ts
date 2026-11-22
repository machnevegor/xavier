import { describe, expect, it } from "vitest";
import type { Question } from "@/api/types.ts";
import {
  type AskedQuestion,
  answerInput,
  EMPTY_ANSWER,
  isQuestionAnswered,
  OTHER_ANSWER,
  questionsOf,
} from "@/features/chats/feed/question-model.ts";

function firstQuestion(items: Question["questions"]): AskedQuestion {
  const [item] = questionsOf(question(items));
  if (!item) {
    throw new Error("expected a parsed question");
  }
  return item;
}

const question = (items: Question["questions"]): Question => ({
  answered_at: null,
  answers: null,
  call_id: "call",
  chat_id: "chat",
  created_at: "2026-11-20T10:00:00Z",
  id: "question",
  questions: items,
  status: "pending",
  timeout_s: -1,
  turn_id: "turn",
  waiting: true,
});

describe("questionsOf", () => {
  it("keeps documented options and separates an Other choice", () => {
    const [parsed] = questionsOf(
      question([
        {
          id: "language",
          multiSelect: true,
          options: [
            { description: "Typed", label: "TypeScript" },
            { label: "Other" },
          ],
          question: "Which languages?",
        },
      ]),
    );

    expect(parsed).toMatchObject({
      id: "language",
      multi: true,
      options: [{ description: "Typed", label: "TypeScript" }],
      other: { label: "Other" },
      question: "Which languages?",
    });
  });

  it("drops malformed items instead of inventing identifiers", () => {
    expect(questionsOf(question([{ question: "Missing id" }, null]))).toEqual(
      [],
    );
  });
});

describe("answerInput", () => {
  const item = firstQuestion([
    {
      id: "choice",
      options: [{ label: "A" }, { label: "Other" }],
      question: "Choose",
    },
  ]);

  it("maps a custom answer to the API contract", () => {
    expect(
      answerInput(item, {
        custom: "B",
        selected: [OTHER_ANSWER],
        skipped: false,
      }),
    ).toEqual({ custom: "B", id: "choice", selected: ["Other"] });
  });

  it("maps an explicit skip to empty selected and custom values", () => {
    expect(answerInput(item, { ...EMPTY_ANSWER, skipped: true })).toEqual({
      custom: null,
      id: "choice",
      selected: [],
    });
    expect(isQuestionAnswered(item, { ...EMPTY_ANSWER, skipped: true })).toBe(
      true,
    );
  });
});
