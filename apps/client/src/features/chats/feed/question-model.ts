import { isRecord, type Question } from "@/api/types.ts";
import { m } from "@/paraglide/messages.js";

export interface QuestionOption {
  label: string;
  description?: string;
}

export interface AskedQuestion {
  id: string;
  header?: string;
  question: string;
  options: QuestionOption[];
  /** The agent's own "Other" option, which opens the text field like ours does. */
  other?: QuestionOption;
  multi: boolean;
}

export interface AnswerDraft {
  selected: string[];
  custom: string;
  skipped: boolean;
}

/** Internal selection value for a custom answer; it is never sent to the API. */
export const OTHER_ANSWER = "\u0000other";
export const EMPTY_ANSWER: AnswerDraft = {
  custom: "",
  selected: [],
  skipped: false,
};

const OTHER_LABEL = /^(other|something else|другое|иное|свой вариант)\b/i;

/** Parse the documented question payload without inventing fields for malformed items. */
export function questionsOf(question: Question): AskedQuestion[] {
  if (!Array.isArray(question.questions)) {
    return [];
  }
  return question.questions.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string") {
      return [];
    }
    const options: QuestionOption[] = Array.isArray(item.options)
      ? item.options.flatMap((option) =>
          isRecord(option) && typeof option.label === "string"
            ? [
                {
                  description:
                    typeof option.description === "string"
                      ? option.description
                      : undefined,
                  label: option.label,
                },
              ]
            : [],
        )
      : [];
    const other = options.find((option) => OTHER_LABEL.test(option.label));
    return [
      {
        header: typeof item.header === "string" ? item.header : undefined,
        id: item.id,
        multi: item.multiSelect === true || item.multi_select === true,
        options: options.filter((option) => option !== other),
        other,
        question:
          typeof item.question === "string" ? item.question : m.chat_question(),
      },
    ];
  });
}

export function isQuestionAnswered(
  item: AskedQuestion,
  draft: AnswerDraft,
): boolean {
  if (draft.skipped) {
    return true;
  }
  if (item.options.length === 0 && !item.other) {
    return draft.custom.trim() !== "";
  }
  const chosen = draft.selected.filter((label) => label !== OTHER_ANSWER);
  if (!draft.selected.includes(OTHER_ANSWER)) {
    return chosen.length > 0;
  }
  return item.other !== undefined || draft.custom.trim() !== "";
}

/** Convert one draft to the exact `AnswerQuestionInput` shape. */
export function answerInput(item: AskedQuestion, draft: AnswerDraft) {
  if (draft.skipped) {
    return { custom: null, id: item.id, selected: [] };
  }
  const other = draft.selected.includes(OTHER_ANSWER);
  const selected = draft.selected.filter((label) => label !== OTHER_ANSWER);
  if (other && item.other) {
    selected.push(item.other.label);
  }
  const custom =
    other || (item.options.length === 0 && !item.other)
      ? draft.custom.trim() || null
      : null;
  return { custom, id: item.id, selected };
}

export function answerText(
  selected: string[],
  custom: string | null | undefined,
) {
  return [...selected, ...(custom ? [`“${custom}”`] : [])].join(", ");
}
