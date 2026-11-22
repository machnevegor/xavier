import type { Chat, ChatStatus, Mode, ToolGroup } from "@/api/types.ts";
import type { StatusMeta } from "@/components/status-text.tsx";
import { m } from "@/paraglide/messages.js";

export const ACTIVE_STATUSES: readonly ChatStatus[] = [
  "working",
  "needs_approval",
  "needs_input",
  "ready",
  "paused",
];

type Text = () => string;

export const CHAT_STATUS: Record<
  ChatStatus,
  { label: Text; tone: StatusMeta["tone"]; pulse?: boolean }
> = {
  needs_approval: { label: m.chat_needs_approval, tone: "attention" },
  needs_input: { label: m.chat_needs_your_answer, tone: "attention" },
  paused: { label: m.chat_paused, tone: "neutral" },
  ready: { label: m.chat_ready, tone: "neutral" },
  working: { label: m.chat_working, pulse: true, tone: "live" },
};

export function chatStatus(chat: Chat): StatusMeta {
  if (chat.status === "paused" && chat.pause_reason === "budget") {
    return { label: m.chat_paused_budget_spent(), tone: "attention" };
  }
  const meta = CHAT_STATUS[chat.status];
  return { label: meta.label(), tone: meta.tone, pulse: meta.pulse };
}

export const MODES: readonly { value: Mode; label: Text; hint: Text }[] = [
  {
    hint: m.chat_asks_before_anything_that_changes_something,
    label: m.chat_supervised,
    value: "supervised",
  },
  {
    hint: m.chat_asks_only_before_sensitive_actions,
    label: m.chat_autonomous,
    value: "autonomous",
  },
  {
    hint: m.chat_may_look_never_change,
    label: m.chat_read_only,
    value: "read_only",
  },
];

export const modeLabel = (mode: Mode): string =>
  MODES.find((option) => option.value === mode)?.label() ?? mode;

/** `files` comes with `execute` or `compute`, so it is not offered on its own. */
export const TOOL_GROUPS: readonly {
  value: ToolGroup;
  label: Text;
  hint: Text;
}[] = [
  {
    hint: m.chat_fetch_pages_and_draft_watches,
    label: m.chat_observe,
    value: "observe",
  },
  {
    hint: m.chat_run_code_in_a_fresh_container,
    label: m.chat_execute,
    value: "execute",
  },
  {
    hint: m.chat_use_a_desktop_browser_and_terminal,
    label: m.chat_compute,
    value: "compute",
  },
];

export const PROFILE_TABS = [
  "media",
  "files",
  "computer",
  "credentials",
  "guardrails",
  "usage",
] as const;
export type ProfileTab = (typeof PROFILE_TABS)[number];

export function isProfileTab(value: unknown): value is ProfileTab {
  return PROFILE_TABS.some((tab) => tab === value);
}

/** The Computer when it is on and the agent may use it, otherwise the chat's files. */
export function defaultProfileTab(chat: Chat): ProfileTab {
  return chat.computer_active && chat.tool_groups.includes("compute")
    ? "computer"
    : "files";
}

/** A turn is under way or waits on a person, so stopping it means something. */
export const canStop = (chat: Chat): boolean =>
  chat.status === "working" ||
  chat.status === "needs_approval" ||
  chat.status === "needs_input";

export function isToolGroup(value: string): value is ToolGroup {
  return (
    TOOL_GROUPS.some((group) => group.value === value) || value === "files"
  );
}
