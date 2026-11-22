import {
  IconChevronLeft,
  IconCpu,
  IconDeviceDesktop,
  IconPlayerPlay,
  IconPlayerStop,
  IconShieldHalf,
  IconTools,
} from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { cn } from "cn";
import { type ReactNode, useEffect, useRef } from "react";
import type { StreamState } from "@/api/stream.ts";
import type { Chat } from "@/api/types.ts";
import { Status } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs.tsx";
import { AgentAvatar, useAgentState } from "@/features/chats/agent-avatar.tsx";
import { ChatCredentials } from "@/features/chats/chat-credentials.tsx";
import { ChatGuardrails } from "@/features/chats/chat-guardrails.tsx";
import { ChatMenu, useChatCommand } from "@/features/chats/chat-menu.tsx";
import {
  canStop,
  chatStatus,
  defaultProfileTab,
  isProfileTab,
  modeLabel,
  type ProfileTab,
  TOOL_GROUPS,
} from "@/features/chats/model.ts";
import {
  ComputerSection,
  FilesSection,
  MediaSection,
  UsageSection,
} from "@/features/chats/profile-sections.tsx";
import { m } from "@/paraglide/messages.js";

const STREAM: Record<StreamState, () => string> = {
  connecting: m.chat_connecting,
  failed: m.chat_offline_reload_the_page_to_reconnect,
  live: m.chat_live,
  reconnecting: m.chat_reconnecting,
};

const STREAM_TEXT: Record<StreamState, string> = {
  connecting: "text-muted-foreground",
  failed: "text-destructive",
  live: "",
  reconnecting: "text-attention",
};

const TABS: { value: ProfileTab; label: () => string }[] = [
  { label: m.chat_media, value: "media" },
  { label: m.chat_files, value: "files" },
  { label: m.chat_computer, value: "computer" },
  { label: m.chat_credentials, value: "credentials" },
  { label: m.chat_guardrails, value: "guardrails" },
  { label: m.chat_usage, value: "usage" },
];

function QuickCommand({ chat }: { chat: Chat }) {
  const command = useChatCommand(chat);
  if (chat.status === "paused") {
    return (
      <Button
        className="mt-3 rounded-full"
        disabled={command.isPending}
        onClick={() => command.mutate("resume")}
        size="sm"
        variant="secondary"
      >
        <IconPlayerPlay data-icon="inline-start" />
        {m.chat_resume()}
      </Button>
    );
  }
  if (canStop(chat)) {
    return (
      <Button
        className="mt-3 rounded-full"
        disabled={command.isPending}
        onClick={() => command.mutate("stop")}
        size="sm"
        title={m.chat_end_the_current_turn_and_cancel_pending_decisions()}
        variant="secondary"
      >
        <IconPlayerStop data-icon="inline-start" />
        {m.chat_stop()}
      </Button>
    );
  }
  return null;
}

function ColumnBar({
  back,
  title,
  actions,
  onBack,
}: {
  back: string;
  title: string;
  actions?: ReactNode;
  onBack: () => void;
}) {
  return (
    <div className="bg-background/95 supports-backdrop-filter:bg-background/80 sticky top-0 z-20 -mx-4 flex h-14 items-center gap-1 border-b px-2 backdrop-blur sm:-mx-8 sm:px-6 lg:-mx-10 lg:px-8">
      <Button
        aria-label={back}
        className="text-muted-foreground"
        onClick={onBack}
        size="icon"
        variant="ghost"
      >
        <IconChevronLeft />
      </Button>
      <h2 className="min-w-0 flex-1 truncate font-medium">{title}</h2>
      {actions}
    </div>
  );
}

/**
 * The chat's profile in the chat column: who the agent is, and its media, files, Computer,
 * credentials, guardrails and usage.
 */
export function ChatProfile({
  chat,
  stream,
  composing,
  tab,
  onTab,
  onBack,
  onSettings,
  onRename,
}: {
  chat: Chat;
  stream: StreamState;
  composing: boolean;
  tab: ProfileTab | undefined;
  onTab: (tab: ProfileTab) => void;
  onBack: () => void;
  onSettings: () => void;
  onRename: () => void;
}) {
  const agent = useAgentState(chat, { composing, stream });
  const tabList = useRef<HTMLDivElement>(null);
  const tools = TOOL_GROUPS.filter((group) =>
    chat.tool_groups.includes(group.value),
  );
  const facts: { label: string; value: ReactNode; icon: ReactNode }[] = [
    {
      icon: <IconCpu />,
      label: m.chat_model_2(),
      value: <span className="font-mono text-[0.8125rem]">{chat.model}</span>,
    },
    {
      icon: <IconShieldHalf />,
      label: m.chat_mode(),
      value: modeLabel(chat.mode),
    },
    {
      icon: <IconTools />,
      label: m.chat_may_use(),
      value: tools.map((group) => group.label()).join(", ") || m.chat_nothing(),
    },
  ];
  const current = tab ?? defaultProfileTab(chat);

  useEffect(() => {
    const list = tabList.current;
    const active = list?.querySelector("[data-active]");
    if (!list || !(active instanceof HTMLElement)) {
      return;
    }
    const left =
      active.offsetLeft - list.clientWidth / 2 + active.clientWidth / 2;
    list.scrollTo({ left });
  }, [current]);

  return (
    <div>
      <ColumnBar
        actions={
          <ChatMenu chat={chat} onRename={onRename} onSettings={onSettings} />
        }
        back={m.chat_back_to_the_conversation()}
        onBack={onBack}
        title={chat.title}
      />
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex flex-col items-center px-2 pt-6 pb-5 text-center">
          <AgentAvatar className="size-20" state={agent} />
          <p className="mt-3 max-w-full text-lg leading-snug font-semibold break-words">
            {chat.title}
          </p>
          <div className="mt-0.5">
            {agent.activity ? (
              <span className="text-ok text-sm">{agent.activity}…</span>
            ) : (
              <Status meta={chatStatus(chat)} />
            )}
          </div>
          {stream !== "live" && (
            <p className={cn("mt-1 text-xs", STREAM_TEXT[stream])}>
              {STREAM[stream]()}
            </p>
          )}
          <QuickCommand chat={chat} />
        </div>
        <dl className="bg-muted/50 rounded-xl py-1">
          {facts.map(({ label, value, icon }) => (
            <div
              className="flex items-center gap-3 px-4 py-2 [&_svg]:size-[1.125rem]"
              key={label}
            >
              <span aria-hidden="true" className="text-muted-foreground">
                {icon}
              </span>
              <div className="flex min-w-0 flex-1 flex-col-reverse">
                <dt className="text-muted-foreground text-xs">{label}</dt>
                <dd className="truncate">{value}</dd>
              </div>
            </div>
          ))}
        </dl>
        <Tabs
          className="mt-5 gap-0"
          onValueChange={(value) => {
            if (isProfileTab(value)) {
              onTab(value);
            }
          }}
          value={current}
        >
          <TabsList
            className="bg-background sticky top-14 z-10 h-11 w-full [scrollbar-width:none] justify-start gap-0 overflow-x-auto overflow-y-hidden border-b px-0 group-data-horizontal/tabs:h-11 [&::-webkit-scrollbar]:hidden"
            ref={tabList}
            variant="line"
          >
            {TABS.map(({ label, value }) => (
              <TabsTrigger className="flex-none px-3" key={value} value={value}>
                {label()}
                {value === "computer" && chat.computer_active && (
                  <span
                    aria-label={m.chat_on()}
                    className="bg-ok size-1.5 rounded-full"
                    role="img"
                  />
                )}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent className="pt-4 pb-10" value="media">
            <MediaSection chatId={chat.id} />
          </TabsContent>
          <TabsContent className="pt-4 pb-10" value="files">
            <FilesSection chat={chat} />
          </TabsContent>
          <TabsContent className="pt-4 pb-10" value="computer">
            <ComputerSection chat={chat} onSettings={onSettings} />
          </TabsContent>
          <TabsContent className="pt-4 pb-10" value="credentials">
            <ChatCredentials chat={chat} />
          </TabsContent>
          <TabsContent className="pt-4 pb-10" value="guardrails">
            <ChatGuardrails chat={chat} />
          </TabsContent>
          <TabsContent className="pt-4 pb-10" value="usage">
            <UsageSection chat={chat} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

/**
 * Stays at the top of the chat like a messenger's conversation bar: who is talking and what it
 * is doing. The profile row opens the chat's profile, the Computer goes straight to its tab,
 * and the menu holds the chat's commands.
 */
export function ChatHeader({
  chat,
  stream,
  composing,
  onOpenProfile,
  onOpenSettings,
  onRename,
}: {
  chat: Chat;
  stream: StreamState;
  composing: boolean;
  onOpenProfile: (tab: ProfileTab) => void;
  onOpenSettings: () => void;
  onRename: () => void;
}) {
  const agent = useAgentState(chat, { composing, stream });
  const streamNote = stream !== "live" ? STREAM[stream]() : null;
  const usesComputer =
    chat.computer_active || chat.tool_groups.includes("compute");

  return (
    <header className="bg-background/95 supports-backdrop-filter:bg-background/80 sticky top-0 z-20 -mx-4 border-b px-2 backdrop-blur sm:-mx-8 sm:px-6 lg:-mx-10 lg:px-8">
      <h1 className="sr-only">{chat.title}</h1>
      <div className="mx-auto flex h-14 max-w-3xl items-center gap-1">
        <Button
          aria-label={m.chat_back_to_work_2()}
          className="text-muted-foreground md:hidden"
          nativeButton={false}
          render={<Link to="/" />}
          size="icon"
          variant="ghost"
        >
          <IconChevronLeft />
        </Button>
        <button
          className="hover:bg-accent/60 focus-visible:ring-ring/50 flex h-12 min-w-0 flex-1 items-center gap-3 rounded-md px-2 text-left outline-none focus-visible:ring-3"
          onClick={() => onOpenProfile(defaultProfileTab(chat))}
          type="button"
        >
          <AgentAvatar className="size-9" state={agent} />
          <span className="min-w-0">
            <span className="sr-only">{m.chat_chat_details()} </span>
            <span className="block truncate font-medium">{chat.title}</span>
            <span
              className={cn(
                "block truncate text-xs",
                streamNote
                  ? STREAM_TEXT[stream]
                  : agent.activity
                    ? "text-ok"
                    : "text-muted-foreground",
              )}
              role="status"
            >
              {streamNote ??
                (agent.activity
                  ? `${agent.activity}…`
                  : chatStatus(chat).label)}
            </span>
          </span>
        </button>
        {usesComputer && (
          <Button
            aria-label={
              chat.computer_active
                ? m.chat_open_computer()
                : m.chat_open_computer_currently_off()
            }
            className="text-muted-foreground"
            onClick={() => onOpenProfile("computer")}
            size={chat.computer_active ? "sm" : "icon"}
            title={
              chat.computer_active
                ? m.chat_open_computer()
                : m.chat_computer_is_off_open_it_to_start()
            }
            variant="ghost"
          >
            <span className="relative inline-flex">
              <IconDeviceDesktop />
              {chat.computer_active && (
                <span
                  aria-hidden="true"
                  className="bg-ok ring-background absolute -right-0.5 -bottom-0.5 size-2 rounded-full ring-2"
                />
              )}
            </span>
            {chat.computer_active && (
              <span className="hidden sm:inline">{m.chat_computer_2()}</span>
            )}
          </Button>
        )}
        <ChatMenu chat={chat} onRename={onRename} onSettings={onOpenSettings} />
      </div>
    </header>
  );
}

export { ColumnBar };
