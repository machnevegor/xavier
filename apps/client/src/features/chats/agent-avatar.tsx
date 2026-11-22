import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { createContext, useContext, useEffect, useState } from "react";
import type { StreamState } from "@/api/stream.ts";
import type { Chat, Turn } from "@/api/types.ts";
import { isActive, liveLabel } from "@/features/actions/model.ts";
import { chatQueries } from "@/features/chats/api.ts";

export type Mood =
  | "idle"
  | "composing"
  | "thinking"
  | "using"
  | "computer"
  | "expecting"
  | "asking"
  | "pleased"
  | "concerned"
  | "resting"
  | "offline";

type Presence = "online" | "working" | "waiting" | "offline";

export interface AgentState {
  mood: Mood;
  presence: Presence | null;
  /** What the agent is doing, in words, while a turn runs. */
  activity: string | null;
}

const FAVICON_BY_MOOD: Record<
  Mood,
  { brows?: string; eyes?: string; joy?: boolean }
> = {
  asking: { brows: "translate(0 -1)", eyes: "scale(1.12)" },
  composing: { eyes: "translate(0 2)" },
  computer: { brows: "translate(0 -1)", eyes: "scale(1 .8) translate(0 2)" },
  concerned: { brows: "rotate(8 12 11)", eyes: "scale(.9) translate(2 2)" },
  expecting: { brows: "translate(0 -1)", eyes: "translate(1 -1)" },
  idle: {},
  offline: { eyes: "scale(1 .35) translate(0 13)" },
  pleased: { joy: true },
  resting: { eyes: "scale(1 .18) translate(0 54)" },
  thinking: { brows: "translate(0 -1)", eyes: "scale(1 .65) translate(0 4)" },
  using: { brows: "translate(0 -1)", eyes: "scale(1.08)" },
};

function favicon(mood: Mood): string {
  const pose = FAVICON_BY_MOOD[mood];
  const opacity = mood === "offline" ? 0.55 : 1;
  const eyes = pose.joy
    ? '<path d="M7 14q2.5-4 5 0m2 0q2.5-4 5 0" fill="none" stroke="white" stroke-linecap="round" stroke-width="1.8"/>'
    : `<g transform="${pose.eyes ?? ""}"><rect x="8" y="10" width="4" height="7" rx="2"/><rect x="16" y="10" width="4" height="7" rx="2"/></g>`;
  const brows = pose.brows
    ? `<g fill="none" stroke="white" stroke-linecap="round" stroke-width="1.4" transform="${pose.brows}"><path d="m8 8 4 1"/><path d="m20 8-4 1"/></g>`
    : "";
  return `data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" opacity="${opacity}"><style>circle{fill:#5a4bb4}g,rect{fill:white}@media(prefers-color-scheme:dark){circle{fill:#b3a9f0}g,rect{fill:#17151f}}</style><circle cx="14" cy="14" r="13"/>${brows}${eyes}</svg>`,
  )}`;
}

/** Keep the browser tab in sync with the same activity and mood shown in the chat header. */
export function AgentDocumentState({
  chat,
  composing,
  stream,
}: {
  chat: Chat;
  composing: boolean;
  stream: StreamState;
}) {
  const state = useAgentState(chat, { composing, stream });

  useEffect(() => {
    document.title = state.activity
      ? `${state.activity} — ${chat.title} | Xavier`
      : `${chat.title} | Xavier`;
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!icon) {
      return;
    }
    icon.href = favicon(state.mood);
    return () => {
      icon.href = "/favicon.svg";
    };
  }, [chat.title, state.activity, state.mood]);

  return null;
}

const PLEASED_MS = 2800;
const COMPUTER_TOOLS = new Set(["browser", "screen", "terminal"]);

/** Whether the operator is writing to the agent, so the face can look down at the message. */
export const ComposingContext = createContext<(composing: boolean) => void>(
  () => {},
);

/** Reports `composing` while mounted; the face only reacts to it when the agent is idle. */
export function useReportComposing(composing: boolean): void {
  const report = useContext(ComposingContext);
  useEffect(() => {
    report(composing);
    return () => report(false);
  }, [report, composing]);
}

/** True for a moment after a turn is seen to complete; turns that ended before stay quiet. */
function useJustCompleted(turns: Turn[] | undefined): boolean {
  const latest = turns?.at(-1);
  const completed = turns
    ? latest?.status === "completed"
      ? latest.id
      : null
    : undefined;
  const [seen, setSeen] = useState(completed);
  const [pleased, setPleased] = useState(false);
  if (completed !== seen) {
    setSeen(completed);
    setPleased(seen !== undefined && completed != null);
  }
  useEffect(() => {
    if (!pleased) {
      return;
    }
    const timer = window.setTimeout(() => setPleased(false), PLEASED_MS);
    return () => window.clearTimeout(timer);
  }, [pleased]);
  return pleased;
}

/**
 * The face and presence of the agent, read from what the backend reports: the chat's status,
 * the calls of the running turn, how the last turn ended and the live connection. Nothing
 * here stands for the model's thinking.
 */
export function useAgentState(
  chat: Chat,
  { composing, stream }: { composing: boolean; stream: StreamState },
): AgentState {
  const turns = useQuery(chatQueries.turns(chat.id)).data?.items;
  const actions = useQuery(chatQueries.actions(chat.id)).data?.items;
  const pleased = useJustCompleted(turns);
  const latest = turns?.at(-1);
  const mine = actions?.filter((action) => action.turn_id === latest?.id) ?? [];
  const current = mine.filter(isActive).at(-1);
  const offline = stream === "failed";

  let mood: Mood;
  if (offline) {
    mood = "offline";
  } else if (
    chat.status === "needs_approval" ||
    chat.status === "needs_input"
  ) {
    mood = "asking";
  } else if (chat.status === "paused") {
    mood = chat.pause_reason === "budget" ? "concerned" : "resting";
  } else if (chat.status === "working") {
    mood = !current
      ? "thinking"
      : current.tool === "job"
        ? "expecting"
        : COMPUTER_TOOLS.has(current.tool)
          ? "computer"
          : "using";
  } else if (composing) {
    mood = "composing";
  } else if (pleased) {
    mood = "pleased";
  } else {
    mood = latest?.status === "failed" ? "concerned" : "idle";
  }

  let presence: Presence | null;
  if (offline) {
    presence = "offline";
  } else if (
    chat.status === "needs_approval" ||
    chat.status === "needs_input" ||
    chat.status === "paused" ||
    stream === "reconnecting"
  ) {
    presence = "waiting";
  } else {
    presence = chat.status === "working" ? "working" : "online";
  }
  const activity = chat.status === "working" ? liveLabel(mine).text : null;
  return { activity, mood, presence };
}

const DOT: Record<Presence, string> = {
  offline: "bg-destructive",
  online: "bg-ok",
  waiting: "bg-attention",
  working: "bg-ok motion-safe:animate-pulse",
};

/**
 * The agent's face: relaxed and glancing about when idle, looking down at a message being
 * written, focused while a turn runs, curious while a tool works, following a screen while it
 * uses the Computer, expectant while it waits on a job, wide-eyed when a decision or an answer
 * waits, briefly pleased when a turn completes, worried when it failed, faded while the
 * connection is lost, and eyes shut while paused.
 */
export function AgentAvatar({
  state,
  className,
}: {
  state: AgentState;
  className?: string;
}) {
  return (
    <span className={cn("relative inline-flex shrink-0", className)}>
      <svg
        aria-hidden="true"
        className="agent-face size-full"
        data-mood={state.mood}
        viewBox="0 0 36 36"
      >
        <circle className="fill-foreground" cx="18" cy="18" r="18" />
        <g className="agent-brows stroke-background" strokeLinecap="round">
          <path
            className="agent-brow"
            d="M10.5 11.2 15.5 12.6"
            strokeWidth="1.6"
          />
          <path
            className="agent-brow"
            d="M25.5 11.2 20.5 12.6"
            strokeWidth="1.6"
          />
        </g>
        <g className="agent-gaze">
          <g className="fill-background">
            <rect
              className="agent-eye"
              height="8"
              rx="2.5"
              width="5"
              x="10.5"
              y="14"
            />
            <rect
              className="agent-eye"
              height="8"
              rx="2.5"
              width="5"
              x="20.5"
              y="14"
            />
          </g>
          <path
            className="agent-joy stroke-background fill-none"
            d="M10.5 19.5Q13 15.5 15.5 19.5M20.5 19.5Q23 15.5 25.5 19.5"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
        </g>
      </svg>
      {state.presence && (
        <span
          aria-hidden="true"
          className={cn(
            "ring-background absolute right-[3%] bottom-[3%] size-[max(0.625rem,15%)] rounded-full ring-2",
            DOT[state.presence],
          )}
        />
      )}
    </span>
  );
}
