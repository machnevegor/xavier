import {
  IconArrowLeft,
  IconCircleCheck,
  IconClockX,
  IconMessageQuestion,
} from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { isRecord, type Question } from "@/api/types.ts";
import { Disclosure } from "@/components/disclosure.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Checkbox } from "@/components/ui/checkbox.tsx";
import { Input } from "@/components/ui/input.tsx";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import { DecisionCard } from "@/features/approvals/decision-card.tsx";
import { answerQuestion } from "@/features/chats/api.ts";
import {
  type AnswerDraft,
  type AskedQuestion,
  answerInput,
  answerText,
  EMPTY_ANSWER,
  isQuestionAnswered,
  OTHER_ANSWER,
  questionsOf,
} from "@/features/chats/feed/question-model.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

function OptionRow({
  id,
  control,
  label,
  description,
}: {
  id: string;
  control: ReactNode;
  label: string;
  description?: string;
}) {
  return (
    <label
      className="hover:bg-muted/60 has-data-checked:bg-primary/5 flex cursor-pointer items-start gap-3 rounded-md px-3 py-2.5 transition-colors has-disabled:pointer-events-none has-disabled:opacity-60"
      htmlFor={id}
    >
      {control}
      <span className="min-w-0 text-sm">
        <span className="block font-medium">{label}</span>
        {description && (
          <span className="text-muted-foreground block">{description}</span>
        )}
      </span>
    </label>
  );
}

/** One question: its options as cards, "Other" opening a text field, or free text alone. */
function Step({
  base,
  item,
  draft,
  disabled,
  onChange,
  onEnter,
}: {
  base: string;
  item: AskedQuestion;
  draft: AnswerDraft;
  disabled: boolean;
  onChange: (draft: AnswerDraft) => void;
  onEnter: () => void;
}) {
  const other = draft.selected.includes(OTHER_ANSWER);
  const freeText = item.options.length === 0 && !item.other;
  const otherInput = useRef<HTMLInputElement>(null);
  const enter = (event: KeyboardEvent) => {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onEnter();
    }
  };
  if (draft.skipped) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">
          {m.chat_skipped()}
        </span>
        <Button
          disabled={disabled}
          onClick={() => onChange(EMPTY_ANSWER)}
          size="xs"
          type="button"
          variant="ghost"
        >
          {m.chat_change()}
        </Button>
      </div>
    );
  }
  const check = (label: string, id: string) => (
    <Checkbox
      checked={draft.selected.includes(label)}
      className="mt-0.5"
      disabled={disabled}
      id={id}
      onCheckedChange={(checked) => {
        const next = new Set(draft.selected);
        if (checked) {
          next.add(label);
        } else {
          next.delete(label);
        }
        onChange({ ...draft, selected: [...next], skipped: false });
        if (checked && label === OTHER_ANSWER) {
          requestAnimationFrame(() => otherInput.current?.focus());
        }
      }}
    />
  );
  const otherRow = (control: ReactNode) => (
    <OptionRow
      control={control}
      description={item.other?.description}
      id={`${base}-other`}
      label={item.other?.label ?? m.chat_other()}
    />
  );

  return (
    <>
      {freeText ? (
        <Textarea
          aria-label={item.question}
          className="max-h-48 min-h-20"
          disabled={disabled}
          maxLength={10_000}
          onChange={(event) =>
            onChange({
              ...draft,
              custom: event.target.value,
              skipped: false,
            })
          }
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              enter(event);
            }
          }}
          placeholder={m.chat_your_answer()}
          value={draft.custom}
        />
      ) : item.multi ? (
        <div className="grid gap-2">
          {item.options.map((option) => (
            <OptionRow
              control={check(option.label, `${base}-${option.label}`)}
              description={option.description}
              id={`${base}-${option.label}`}
              key={option.label}
              label={option.label}
            />
          ))}
          {otherRow(check(OTHER_ANSWER, `${base}-other`))}
        </div>
      ) : (
        <RadioGroup
          aria-label={item.question}
          className="grid gap-2"
          disabled={disabled}
          onValueChange={(value) => {
            if (typeof value === "string") {
              onChange({ ...draft, selected: [value], skipped: false });
              if (value === OTHER_ANSWER) {
                requestAnimationFrame(() => otherInput.current?.focus());
              }
            }
          }}
          value={draft.selected[0] ?? null}
        >
          {item.options.map((option) => (
            <OptionRow
              control={
                <RadioGroupItem
                  className="mt-0.5"
                  id={`${base}-${option.label}`}
                  value={option.label}
                />
              }
              description={option.description}
              id={`${base}-${option.label}`}
              key={option.label}
              label={option.label}
            />
          ))}
          {otherRow(
            <RadioGroupItem
              className="mt-0.5"
              id={`${base}-other`}
              value={OTHER_ANSWER}
            />,
          )}
        </RadioGroup>
      )}
      {other && (
        <Input
          aria-label={
            item.other
              ? m.chat_details({ label: item.other.label })
              : m.chat_other()
          }
          className="animate-in fade-in-0 slide-in-from-top-1 mt-2 duration-150"
          disabled={disabled}
          maxLength={10_000}
          onChange={(event) =>
            onChange({
              ...draft,
              custom: event.target.value,
              skipped: false,
            })
          }
          onKeyDown={enter}
          placeholder={
            item.other ? m.chat_add_details_optional() : m.chat_your_answer()
          }
          ref={otherInput}
          value={draft.custom}
        />
      )}
      <Button
        disabled={disabled}
        onClick={() => onChange({ custom: "", selected: [], skipped: true })}
        size="xs"
        type="button"
        variant="ghost"
      >
        {m.chat_skipped()}
      </Button>
    </>
  );
}

function Pending({
  question,
  asked,
}: {
  question: Question;
  asked: AskedQuestion[];
}) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, AnswerDraft>>({});
  const [step, setStep] = useState(0);
  const legend = useRef<HTMLParagraphElement>(null);
  const moved = useRef(false);
  const answer = useMutation({
    mutationFn: () =>
      answerQuestion(
        question.id,
        asked.map((item) => answerInput(item, drafts[item.id] ?? EMPTY_ANSWER)),
      ),
    onSettled: () =>
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === "chat" && queryKey[1] === question.chat_id,
      }),
  });
  const count = asked.length;
  const inline =
    count > 1 &&
    count <= 3 &&
    asked.every(
      (each) =>
        each.options.length <= 4 &&
        each.options.every((option) => (option.description?.length ?? 0) <= 80),
    );
  const reviewing = !inline && count > 1 && step === count;
  const item = asked[Math.min(step, count - 1)];
  const draft = (item && drafts[item.id]) ?? EMPTY_ANSWER;
  const valid = item ? isQuestionAnswered(item, draft) : false;
  const allValid = asked.every((each) =>
    isQuestionAnswered(each, drafts[each.id] ?? EMPTY_ANSWER),
  );
  const locked = answer.isPending || answer.isSuccess;

  useEffect(() => {
    if (moved.current) {
      legend.current?.focus();
    }
  }, [step]);

  const go = (next: number) => {
    moved.current = true;
    setStep(next);
  };
  const submit = () => {
    if (!locked && allValid) {
      answer.mutate();
    }
  };
  const forward = () => {
    if (!valid) {
      return;
    }
    if (count === 1) {
      submit();
    } else {
      go(step + 1);
    }
  };

  const primary =
    inline || reviewing ? (
      <Button disabled={locked || !allValid} onClick={submit} size="sm">
        {locked ? m.chat_sending() : m.chat_send_answers()}
      </Button>
    ) : count === 1 ? (
      <Button disabled={locked || !valid} onClick={submit} size="sm">
        {locked ? m.chat_sending() : m.chat_send_answer()}
      </Button>
    ) : (
      <Button disabled={locked || !valid} onClick={forward} size="sm">
        {step === count - 1 ? m.chat_review() : m.chat_next()}
      </Button>
    );

  return (
    <DecisionCard
      ariaLabel={m.chat_question_from_the_agent()}
      busy={answer.isPending}
      footer={
        <>
          {!inline && step > 0 && (
            <Button
              disabled={locked}
              onClick={() => go(step - 1)}
              size="sm"
              variant="ghost"
            >
              <IconArrowLeft data-icon="inline-start" />
              {m.chat_back()}
            </Button>
          )}
          {!inline && count > 1 && (
            <div aria-hidden="true" className="flex gap-1">
              {asked.map((each, index) => (
                <span
                  className={cn(
                    "size-1.5 rounded-full transition-colors",
                    index === step
                      ? "bg-primary"
                      : isQuestionAnswered(
                            each,
                            drafts[each.id] ?? EMPTY_ANSWER,
                          )
                        ? "bg-primary/40"
                        : "bg-muted-foreground/25",
                  )}
                  key={each.id}
                />
              ))}
            </div>
          )}
          <div className="ml-auto">{primary}</div>
        </>
      }
      icon={<IconMessageQuestion aria-hidden="true" />}
      label={
        question.waiting ? m.chat_needs_your_answer_2() : m.chat_question_2()
      }
      meta={
        inline
          ? timeAgo(question.created_at)
          : count > 1
            ? reviewing
              ? m.chat_review_2()
              : m.chat_of_3({ count, step: step + 1 })
            : timeAgo(question.created_at)
      }
    >
      {inline ? (
        <div className="space-y-5">
          {asked.map((each) => {
            const eachDraft = drafts[each.id] ?? EMPTY_ANSWER;
            return (
              <fieldset
                aria-labelledby={`${question.id}-${each.id}-question`}
                className="min-w-0 space-y-3"
                key={each.id}
              >
                {each.header && (
                  <span className="bg-muted text-muted-foreground inline-block rounded-md px-1.5 py-0.5 text-xs font-medium">
                    {each.header}
                  </span>
                )}
                <p
                  className="font-medium"
                  id={`${question.id}-${each.id}-question`}
                >
                  {each.question}
                  {each.multi && (
                    <span className="text-muted-foreground font-normal">
                      {" "}
                      {m.chat_select_all_that_apply()}
                    </span>
                  )}
                </p>
                <Step
                  base={`${question.id}-${each.id}`}
                  disabled={locked}
                  draft={eachDraft}
                  item={each}
                  onChange={(next) =>
                    setDrafts((current) => ({
                      ...current,
                      [each.id]: next,
                    }))
                  }
                  onEnter={submit}
                />
              </fieldset>
            );
          })}
        </div>
      ) : reviewing ? (
        <div className="animate-in fade-in-0 duration-200" key="review">
          <p className="font-medium outline-none" ref={legend} tabIndex={-1}>
            {m.chat_check_your_answers()}
          </p>
          <ol className="mt-2 divide-y">
            {asked.map((each, index) => {
              const given = answerInput(each, drafts[each.id] ?? EMPTY_ANSWER);
              return (
                <li className="flex items-start gap-3 py-2" key={each.id}>
                  <div className="min-w-0 flex-1 text-sm">
                    <p className="text-muted-foreground">
                      {each.header ?? each.question}
                    </p>
                    <p className="break-words">
                      {answerText(given.selected, given.custom)}
                    </p>
                  </div>
                  <Button
                    aria-label={m.chat_change_the_answer_to({
                      question: each.question,
                    })}
                    disabled={locked}
                    onClick={() => go(index)}
                    size="xs"
                    variant="ghost"
                  >
                    {m.chat_change()}
                  </Button>
                </li>
              );
            })}
          </ol>
        </div>
      ) : (
        item && (
          <fieldset
            aria-labelledby={`${question.id}-${item.id}-question`}
            className="animate-in fade-in-0 min-w-0 space-y-3 duration-200"
            key={item.id}
          >
            {item.header && (
              <span className="bg-muted text-muted-foreground inline-block rounded-md px-1.5 py-0.5 text-xs font-medium">
                {item.header}
              </span>
            )}
            <p
              className="font-medium outline-none"
              id={`${question.id}-${item.id}-question`}
              ref={legend}
              tabIndex={-1}
            >
              {item.question}
              {item.multi && (
                <span className="text-muted-foreground font-normal">
                  {" "}
                  {m.chat_select_all_that_apply()}
                </span>
              )}
            </p>
            <Step
              base={`${question.id}-${item.id}`}
              disabled={locked}
              draft={draft}
              item={item}
              onChange={(next) =>
                setDrafts((current) => ({ ...current, [item.id]: next }))
              }
              onEnter={forward}
            />
          </fieldset>
        )
      )}
      {answer.isError && (
        <div className="mt-3">
          <ErrorState error={answer.error} onRetry={submit} />
        </div>
      )}
    </DecisionCard>
  );
}

function Answered({
  question,
  asked,
}: {
  question: Question;
  asked: AskedQuestion[];
}) {
  const given = new Map<string, string>();
  if (Array.isArray(question.answers)) {
    for (const item of question.answers) {
      if (isRecord(item) && typeof item.id === "string") {
        const selected = Array.isArray(item.selected)
          ? item.selected.filter(
              (label): label is string => typeof label === "string",
            )
          : [];
        const custom = typeof item.custom === "string" ? item.custom : null;
        given.set(item.id, answerText(selected, custom) || m.chat_skipped());
      }
    }
  }
  return (
    <DecisionCard
      ariaLabel={m.chat_answered_question()}
      icon={<IconCircleCheck aria-hidden="true" />}
      label={m.chat_answered()}
      meta={question.answered_at ? timeAgo(question.answered_at) : undefined}
      tone="ok"
    >
      <dl className="space-y-1.5 text-sm">
        {asked.map((item) => (
          <div key={item.id}>
            <dt className="text-muted-foreground">{item.question}</dt>
            <dd className="break-words">
              {given.get(item.id) ?? m.common_text()}
            </dd>
          </div>
        ))}
      </dl>
    </DecisionCard>
  );
}

function Closed({
  question,
  asked,
}: {
  question: Question;
  asked: AskedQuestion[];
}) {
  return (
    <DecisionCard
      ariaLabel={m.chat_question_no_longer_open()}
      icon={<IconClockX aria-hidden="true" />}
      label={
        question.status === "expired"
          ? m.chat_not_answered_in_time()
          : m.chat_question_withdrawn()
      }
      meta={timeAgo(question.created_at)}
      tone="muted"
    >
      <Disclosure
        label={
          asked.length === 1
            ? m.chat_show_the_question()
            : m.chat_show_the_questions()
        }
      >
        <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
          {asked.map((item) => (
            <li key={item.id}>{item.question}</li>
          ))}
        </ul>
      </Disclosure>
    </DecisionCard>
  );
}

/**
 * One `ask_user_question` call. Open questions go one at a time, with a review before sending
 * when there are several; answered ones shrink to what was chosen.
 */
export function QuestionCard({ question }: { question: Question }) {
  const asked = useMemo(() => questionsOf(question), [question]);
  if (question.status === "pending") {
    return <Pending asked={asked} question={question} />;
  }
  if (question.status === "answered") {
    return <Answered asked={asked} question={question} />;
  }
  return <Closed asked={asked} question={question} />;
}
