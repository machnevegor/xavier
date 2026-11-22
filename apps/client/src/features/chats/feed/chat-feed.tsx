import {
  IconChevronRight,
  IconLink,
  IconPencil,
  IconRefresh,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  type AnimationEvent,
  Fragment,
  useEffect,
  useMemo,
  useState,
} from "react";
import type {
  Action,
  Chat,
  ChatEvent,
  Message,
  Question,
  Turn,
} from "@/api/types.ts";
import { CopyButton } from "@/components/copy-button.tsx";
import { Markdown } from "@/components/markdown.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { StatusText, type Tone } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import { toast } from "@/components/ui/toast.tsx";
import {
  activitySummary,
  deliveredArtifactIds,
  isActive,
  isWaiting,
  liveLabel,
} from "@/features/actions/model.ts";
import { ActionRequestCard } from "@/features/approvals/action-request-card.tsx";
import { ArtifactChips } from "@/features/artifacts/artifact-list.tsx";
import {
  chatKeys,
  chatQueries,
  regenerateMessage,
  reviseMessage,
} from "@/features/chats/api.ts";
import { ActionStep } from "@/features/chats/feed/action-step.tsx";
import {
  assistantEvent,
  embedsOf,
  mediaOf,
  mediaUrl,
  textOf,
} from "@/features/chats/feed/answer-media.ts";
import {
  buildFeed,
  type FeedItem,
  OUTCOME,
} from "@/features/chats/feed/build-feed.ts";
import { QuestionCard } from "@/features/chats/feed/question-card.tsx";
import { copyText } from "@/lib/clipboard.ts";
import { day, hourMinute } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const NO_EVENTS: ChatEvent[] = [];
const NO_ACTIONS: Action[] = [];
const NO_QUESTIONS: Question[] = [];

function clearHighlight(event: AnimationEvent<HTMLElement>) {
  if (event.target === event.currentTarget) {
    event.currentTarget.removeAttribute("data-highlighted");
  }
}

function shareLink(href: string) {
  copyText(href)
    .then(() => toast.add({ title: m.chat_link_copied(), type: "success" }))
    .catch(() =>
      toast.add({
        title: m.common_cant_copy_in_this_browser_window(),
        type: "error",
      }),
    );
}

/**
 * What is happening right now, as the backend reports it. It moves only while work is under
 * way; waiting for a person is stated plainly and stays still.
 */
export function LiveLabel({
  text,
  tone = "live",
}: {
  text: string;
  tone?: Tone;
}) {
  return (
    <span
      aria-live="polite"
      className={
        tone === "attention" ? "text-attention font-medium" : "shimmer-text"
      }
    >
      {text}…
    </span>
  );
}

function UserMessage({
  message,
  chat,
  highlight,
}: {
  message: Message;
  chat: Chat;
  highlight: boolean;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(message.text);
  const attached = [
    ...message.image_artifact_ids,
    ...message.file_artifact_ids,
  ];
  const canRevise = message.kind === "user" && message.status === "consumed";
  const settle = () =>
    void queryClient.invalidateQueries({ queryKey: chatKeys.chat(chat.id) });
  const revise = useMutation({
    mutationFn: () =>
      reviseMessage(chat.id, message.id, {
        file_artifact_ids: message.file_artifact_ids,
        image_artifact_ids: message.image_artifact_ids,
        text: text.trim(),
      }),
    onSuccess: () => {
      setEditing(false);
      settle();
    },
  });
  const regenerate = useMutation({
    mutationFn: () => regenerateMessage(chat.id, message.id),
    onSuccess: settle,
  });
  return (
    <div
      className="group/message ml-auto flex max-w-[85%] min-w-0 flex-col items-end rounded-lg"
      data-highlighted={highlight ? "true" : undefined}
      id={`message-${message.id}`}
      onAnimationEnd={clearHighlight}
    >
      {editing ? (
        <form
          className="bg-card w-full max-w-full rounded-lg border px-4 py-2.5"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) {
              revise.mutate();
            }
          }}
        >
          <Textarea
            className="min-h-20 resize-none"
            onChange={(event) => setText(event.target.value)}
            value={text}
          />
          {revise.isError && (
            <div className="mt-2">
              <ErrorState error={revise.error} />
            </div>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              disabled={revise.isPending || text.trim() === ""}
              size="sm"
              type="submit"
            >
              {revise.isPending
                ? m.chat_saving_3()
                : m.chat_save_and_regenerate()}
            </Button>
            <Button
              disabled={revise.isPending}
              onClick={() => {
                setText(message.text);
                setEditing(false);
              }}
              size="sm"
              type="button"
              variant="outline"
            >
              {m.chat_cancel_2()}
            </Button>
          </div>
        </form>
      ) : (
        <div className="bg-card max-w-full rounded-lg border px-4 py-2.5">
          {message.text && (
            <p className="text-base break-words whitespace-pre-wrap">
              {message.text}
            </p>
          )}
          {attached.length > 0 && (
            <ArtifactChips
              className={message.text ? "mt-2" : undefined}
              ids={attached}
            />
          )}
          <p className="text-muted-foreground mt-1 text-xs">
            {message.status === "queued"
              ? m.chat_queued()
              : message.status === "superseded"
                ? m.chat_replaced_by_an_edit()
                : hourMinute(message.created_at)}
          </p>
        </div>
      )}
      <div className="-mr-1.5 flex flex-wrap items-center justify-end gap-0.5 transition-opacity group-focus-within/message:opacity-100 group-hover/message:opacity-100 sm:mt-0.5 pointer-fine:opacity-0">
        {message.text && (
          <CopyButton label={m.chat_copy_message()} value={message.text} />
        )}
        <Button
          aria-label={m.chat_share_message()}
          onClick={() =>
            shareLink(
              `${window.location.origin}/chats/${chat.id}?message=${message.id}`,
            )
          }
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          <IconLink aria-hidden="true" />
        </Button>
        {canRevise && !editing && (
          <>
            <Button
              aria-label={m.chat_regenerate_answer()}
              disabled={regenerate.isPending}
              onClick={() => regenerate.mutate()}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <IconRefresh aria-hidden="true" />
            </Button>
            <Button
              aria-label={m.chat_edit_message()}
              disabled={regenerate.isPending}
              onClick={() => {
                setText(message.text);
                setEditing(true);
              }}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <IconPencil aria-hidden="true" />
            </Button>
          </>
        )}
      </div>
      {regenerate.isError && (
        <div className="mt-1 w-full">
          <ErrorState error={regenerate.error} />
        </div>
      )}
    </div>
  );
}

/**
 * A turn's tool calls as one line. While the turn runs the line says what is under way;
 * afterwards how many calls there were, naming any that did not go through.
 */
function TurnActivity({
  actions,
  events,
  live,
}: {
  actions: Action[];
  events: ChatEvent[];
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const steps = actions.filter((action) => !isWaiting(action));
  const status = live ? liveLabel(actions) : null;
  if (steps.length === 0) {
    return status ? <LiveLabel {...status} /> : null;
  }
  const summary = activitySummary(steps);
  return (
    <Collapsible onOpenChange={setOpen} open={open}>
      <CollapsibleTrigger className="text-muted-foreground hover:text-foreground -ml-1 flex min-h-11 max-w-full flex-wrap items-center gap-x-3 rounded px-1 text-left sm:min-h-9">
        <span className="inline-flex items-center gap-1.5">
          <IconChevronRight
            aria-hidden="true"
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              open && "rotate-90",
            )}
          />
          {status ? <LiveLabel {...status} /> : summary.text}
        </span>
        {status && <span>{summary.text}</span>}
        {summary.problems.map((problem) => (
          <StatusText key={problem.label} tone={problem.tone}>
            {problem.label}
          </StatusText>
        ))}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol
          aria-label={m.chat_tool_calls_in_this_turn()}
          className="border-border/80 mt-2 ml-0.5 border-l pl-2"
        >
          {steps.map((action) => (
            <ActionStep action={action} events={events} key={action.id} />
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** The agent's reply, with the files its turn delivered attached below it. */
function Answer({
  turn,
  actions,
  events,
  highlight,
}: {
  turn: Turn;
  actions: Action[];
  events: ChatEvent[];
  highlight: boolean;
}) {
  const event = useMemo(
    () => assistantEvent(events, turn.id),
    [events, turn.id],
  );
  const embeds = useMemo(() => embedsOf(event), [event]);
  const mediaUrls = useMemo(() => mediaOf(event), [event]);
  const media = useMemo(() => {
    const map = new Map<string, string>();
    if (!event) {
      return map;
    }
    mediaUrls.forEach((url, index) => {
      map.set(url, mediaUrl(turn.chat_id, event.id, index));
    });
    return map;
  }, [event, mediaUrls, turn.chat_id]);
  const embedMap = useMemo(() => new Map(Object.entries(embeds)), [embeds]);
  const embedArtifacts = Object.values(embeds)
    .filter((embed) => embed.status === "ok" && embed.artifact_id)
    .map((embed) => embed.artifact_id as string);
  const delivered = useMemo(
    () =>
      deliveredArtifactIds(
        actions.filter((action) => action.turn_id === turn.id),
        events,
      ),
    [actions, events, turn.id],
  );
  const chips = [...new Set([...delivered, ...embedArtifacts])];
  const running = turn.status === "running";
  const text = turn.final_response || textOf(event);

  return (
    <div
      aria-busy={running}
      className="group/answer max-w-prose min-w-0 rounded-lg"
      data-highlighted={highlight && event ? "true" : undefined}
      id={event ? `event-${event.id}` : undefined}
      onAnimationEnd={clearHighlight}
    >
      <Markdown embeds={embedMap} media={media}>
        {text}
      </Markdown>
      {chips.length > 0 && (
        <ArtifactChips
          className="mt-3"
          ids={chips}
          imageClassName="max-h-72 max-w-full"
        />
      )}
      {!running && text && (
        <div className="-ml-1.5 flex items-center gap-0.5 transition-opacity group-focus-within/answer:opacity-100 group-hover/answer:opacity-100 pointer-fine:opacity-0">
          <CopyButton label={m.chat_copy_answer()} value={text} />
          {event && (
            <Button
              aria-label={m.chat_share_answer()}
              onClick={() =>
                shareLink(
                  `${window.location.origin}/chats/${turn.chat_id}?event=${event.id}`,
                )
              }
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <IconLink aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function TurnOutcome({ turn }: { turn: Turn }) {
  return (
    <p
      className={cn(
        "border-l-2 py-0.5 pl-3",
        turn.status === "failed"
          ? "border-destructive"
          : "border-attention text-muted-foreground",
      )}
    >
      {OUTCOME[turn.status]?.(turn)}
    </p>
  );
}

/**
 * Each kind of entry has its own form: your message is a card, the agent's answer is plain
 * prose, its tool use is one quiet line that opens, a decision is a card with buttons, and a
 * turn that ended badly is a note.
 */
function Entry({
  item,
  actions,
  events,
  live,
  chat,
  highlightMessageId,
  highlightEventId,
}: {
  item: FeedItem;
  actions: Action[];
  events: ChatEvent[];
  live: boolean;
  chat: Chat;
  highlightMessageId?: string;
  highlightEventId?: string;
}) {
  switch (item.type) {
    case "message":
      return (
        <UserMessage
          chat={chat}
          highlight={item.value.id === highlightMessageId}
          message={item.value}
        />
      );
    case "activity":
      return (
        <TurnActivity actions={item.actions} events={events} live={live} />
      );
    case "approval":
      return <ActionRequestCard action={item.value} />;
    case "question":
      return <QuestionCard question={item.value} />;
    case "outcome":
      return <TurnOutcome turn={item.value} />;
    case "answer":
      return (
        <Answer
          actions={actions}
          events={events}
          highlight={
            highlightEventId !== undefined &&
            assistantEvent(events, item.value.id)?.id === highlightEventId
          }
          turn={item.value}
        />
      );
  }
}

export function ChatFeed({
  chat,
  highlightMessageId,
  highlightEventId,
  onHighlight,
}: {
  chat: Chat;
  highlightMessageId?: string;
  highlightEventId?: string;
  /** Called before the feed scrolls to the highlighted message or answer. */
  onHighlight: () => void;
}) {
  const messages = useQuery(chatQueries.messages(chat.id));
  const turns = useQuery(chatQueries.turns(chat.id));
  const actions = useQuery(chatQueries.actions(chat.id));
  const questions = useQuery(chatQueries.questions(chat.id));
  const eventsQuery = useQuery(chatQueries.events(chat.id));
  const events = eventsQuery.data?.items ?? NO_EVENTS;
  const feed = useMemo(
    () =>
      buildFeed(
        messages.data?.items ?? [],
        turns.data?.items ?? [],
        actions.data?.items ?? [],
        questions.data?.items ?? NO_QUESTIONS,
        events,
      ),
    [messages.data, turns.data, actions.data, questions.data, events],
  );
  const working = chat.status === "working";
  const showsPendingQuestion = feed.some(
    (item) =>
      item.type === "question" &&
      item.value.status === "pending" &&
      item.value.waiting,
  );
  const liveKey = working
    ? feed.findLast(
        (item) =>
          item.type === "activity" &&
          (item.turn?.status === "running" || item.actions.some(isActive)),
      )?.key
    : undefined;

  useEffect(() => {
    if (messages.isPending || (highlightEventId && eventsQuery.isPending)) {
      return;
    }
    const id = highlightMessageId
      ? `message-${highlightMessageId}`
      : highlightEventId
        ? `event-${highlightEventId}`
        : null;
    if (!id) {
      return;
    }
    const node = document.getElementById(id);
    if (node) {
      onHighlight();
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [
    events,
    eventsQuery.isPending,
    highlightEventId,
    highlightMessageId,
    messages.isPending,
    onHighlight,
  ]);

  const failed = [messages, turns, actions, questions].find(
    (query) => query.isError,
  );
  if (failed) {
    return (
      <ErrorState error={failed.error} onRetry={() => void failed.refetch()} />
    );
  }
  if (
    messages.isPending ||
    turns.isPending ||
    actions.isPending ||
    questions.isPending
  ) {
    return <LoadingRows rows={4} />;
  }

  return (
    <div className="space-y-5">
      <ol aria-label={m.chat_conversation()} className="space-y-5">
        {feed.map((item, index) => {
          const at = new Date(item.at);
          const previous = feed[index - 1];
          const newDay =
            !previous ||
            new Date(previous.at).toDateString() !== at.toDateString();
          return (
            <Fragment key={item.key}>
              {newDay && (
                <li className="text-muted-foreground flex items-center gap-3 text-xs">
                  <span aria-hidden="true" className="bg-border h-px flex-1" />
                  <time dateTime={item.at}>{day(at)}</time>
                  <span aria-hidden="true" className="bg-border h-px flex-1" />
                </li>
              )}
              <li className="flex flex-col">
                <Entry
                  actions={actions.data?.items ?? NO_ACTIONS}
                  chat={chat}
                  events={events}
                  highlightEventId={highlightEventId}
                  highlightMessageId={highlightMessageId}
                  item={item}
                  live={item.key === liveKey}
                />
              </li>
            </Fragment>
          );
        })}
      </ol>
      <div className="min-h-5">
        {working && !liveKey && <LiveLabel text={m.chat_working_2()} />}
        {chat.status === "needs_input" && !showsPendingQuestion && (
          <StatusText tone="attention">
            {m.chat_waiting_for_your_answer()}
          </StatusText>
        )}
      </div>
    </div>
  );
}
