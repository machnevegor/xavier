import type { Icon } from "@tabler/icons-react";
import { IconFiles, IconPhoto } from "@tabler/icons-react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";
import { flatten } from "@/api/query.ts";
import type { Chat, TurnStatus } from "@/api/types.ts";
import { ErrorState, LoadingRows, LoadMore } from "@/components/states.tsx";
import { StatusText, type Tone } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty.tsx";
import { Progress } from "@/components/ui/progress.tsx";
import { ArtifactList } from "@/features/artifacts/artifact-list.tsx";
import { MediaPreview } from "@/features/artifacts/image-preview.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import { galleryTiles } from "@/features/chats/media.ts";
import { WORKSPACE_ROOT, workspaceQueries } from "@/features/workspace/api.ts";
import { ComputerScreen } from "@/features/workspace/computer-screen.tsx";
import { ComputerStatusPanel } from "@/features/workspace/computer-status.tsx";
import { FileBrowser } from "@/features/workspace/file-browser.tsx";
import { WorkspacePanel } from "@/features/workspace/workspace-panel.tsx";
import { between, count, dateTime, seconds, usd } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** A tab with nothing in it yet: one icon, one line of what will appear. */
export function ProfileEmpty({
  icon: Glyph,
  title,
  children,
}: {
  icon: Icon;
  title: string;
  children?: ReactNode;
}) {
  return (
    <Empty className="gap-3 p-6">
      <EmptyHeader className="gap-1">
        <EmptyMedia className="mb-1" variant="icon">
          <Glyph aria-hidden="true" className="size-5" />
        </EmptyMedia>
        <EmptyTitle className="text-sm">{title}</EmptyTitle>
        {children && (
          <EmptyDescription className="text-xs">{children}</EmptyDescription>
        )}
      </EmptyHeader>
    </Empty>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <h3 className="text-muted-foreground mb-1 text-xs font-medium">
      {children}
    </h3>
  );
}

export function ComputerSection({
  chat,
  onSettings,
}: {
  chat: Chat;
  onSettings: () => void;
}) {
  const workspace = useQuery(workspaceQueries.detail(chat.id));
  return (
    <div className="space-y-4">
      {!chat.tool_groups.includes("compute") && (
        <div className="bg-muted/50 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-3 py-2 text-sm">
          <p className="text-muted-foreground min-w-0 flex-1">
            {m.chat_the_agent_cant_use_the_computer_in_this()}
          </p>
          <Button onClick={onSettings} size="xs" variant="outline">
            {m.chat_allow_in_settings()}
          </Button>
        </div>
      )}
      {workspace.isPending ? (
        <LoadingRows rows={1} />
      ) : workspace.isError ? (
        <ErrorState
          error={workspace.error}
          onRetry={() => void workspace.refetch()}
        />
      ) : workspace.data?.status === "hibernated" ? (
        <WorkspacePanel chatId={chat.id} workspace={workspace.data} />
      ) : (
        <ComputerScreen
          chatId={chat.id}
          key={chat.id}
          running={Boolean(chat.computer_active)}
        />
      )}
      <section
        aria-label={m.chat_diagnostics()}
        className="bg-muted/40 rounded-lg px-3 py-2"
      >
        <ComputerStatusPanel chatId={chat.id} />
      </section>
    </div>
  );
}

export function FilesSection({ chat }: { chat: Chat }) {
  const [path, setPath] = useState(WORKSPACE_ROOT);
  const workspace = useQuery(workspaceQueries.detail(chat.id));
  const status = workspace.data?.status;
  return (
    <div className="space-y-6">
      {workspace.isPending ? (
        <LoadingRows rows={2} />
      ) : workspace.isError ? (
        <ErrorState
          error={workspace.error}
          onRetry={() => void workspace.refetch()}
        />
      ) : workspace.data ? (
        <section aria-labelledby="workspace-heading" className="space-y-3">
          <Heading>
            <span id="workspace-heading">{m.chat_workspace()}</span>
          </Heading>
          <WorkspacePanel chatId={chat.id} workspace={workspace.data} />
          {status === "active" && (
            <FileBrowser chatId={chat.id} onOpen={setPath} path={path} />
          )}
        </section>
      ) : null}
      <section aria-labelledby="delivered-heading">
        <Heading>
          <span id="delivered-heading">{m.chat_delivered()}</span>
        </Heading>
        <ArtifactList
          chatId={chat.id}
          empty={
            <ProfileEmpty icon={IconFiles} title={m.chat_no_files_yet()}>
              {m.chat_files_you_upload_or_the_agent_exports_stay()}
            </ProfileEmpty>
          }
        />
      </section>
    </div>
  );
}

/** Images of the chat, newest first, in a square grid. */
export function MediaSection({ chatId }: { chatId: string }) {
  const media = useInfiniteQuery(chatQueries.media(chatId));
  const tiles = useMemo(() => galleryTiles(flatten(media.data)), [media.data]);

  if (media.isPending) {
    return <LoadingRows rows={2} />;
  }
  if (media.isError) {
    return (
      <ErrorState error={media.error} onRetry={() => void media.refetch()} />
    );
  }
  if (tiles.length === 0 && !media.hasNextPage) {
    return (
      <ProfileEmpty icon={IconPhoto} title={m.chat_no_media_yet()}>
        {m.chat_screenshots_and_images_from_the_agents_replies_appear()}
      </ProfileEmpty>
    );
  }
  return (
    <>
      <ul
        aria-label={m.chat_media_2()}
        className="grid grid-cols-2 gap-1 sm:grid-cols-3"
      >
        {tiles.map((item) => (
          <li key={item.id}>
            <MediaPreview
              name={item.name}
              src={item.src}
              trigger={
                <button
                  aria-label={m.chat_open({ name: item.name })}
                  className="bg-muted focus-visible:ring-ring/50 block aspect-square w-full overflow-hidden rounded-md outline-none focus-visible:ring-3"
                  type="button"
                >
                  <img
                    alt=""
                    className="size-full object-cover transition-transform duration-200 hover:scale-105"
                    loading="lazy"
                    src={item.src}
                  />
                </button>
              }
            />
          </li>
        ))}
      </ul>
      <LoadMore {...media} />
    </>
  );
}

const OUTCOME: Record<TurnStatus, { label: () => string; tone: Tone }> = {
  completed: { label: m.chat_completed, tone: "ok" },
  failed: { label: m.chat_failed, tone: "danger" },
  interrupted: { label: m.chat_interrupted, tone: "neutral" },
  limited: { label: m.chat_hit_a_limit, tone: "attention" },
  running: { label: m.chat_running, tone: "live" },
  stopped: { label: m.chat_stopped, tone: "neutral" },
  suspended: { label: m.chat_waiting, tone: "attention" },
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-muted/50 rounded-lg px-3 py-2">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

export function UsageSection({ chat }: { chat: Chat }) {
  const turns = useQuery(chatQueries.turns(chat.id));
  const share =
    chat.budget_usd > 0
      ? Math.min(100, (chat.spent_usd / chat.budget_usd) * 100)
      : 0;
  const items = turns.data?.items ?? [];
  const total = items.reduce(
    (sum, turn) => ({
      input: sum.input + turn.input_tokens,
      output: sum.output + turn.output_tokens,
      steps: sum.steps + turn.steps,
      time:
        sum.time +
        (turn.finished_at
          ? (Date.parse(turn.finished_at) - Date.parse(turn.started_at)) / 1000
          : 0),
    }),
    { input: 0, output: 0, steps: 0, time: 0 },
  );

  return (
    <div className="space-y-6">
      <section aria-label={m.chat_budget()}>
        <p className="type-figure">
          {m.chat_of({
            budget: usd(chat.budget_usd),
            spent: usd(chat.spent_usd),
          })}
        </p>
        <Progress
          aria-label={m.chat_share_of_budget_spent()}
          className="mt-2"
          value={share}
        />
        <p
          className={
            chat.pause_reason === "budget"
              ? "text-attention mt-2 text-xs"
              : "text-muted-foreground mt-2 text-xs"
          }
        >
          {chat.pause_reason === "budget"
            ? m.chat_budget_used_up_so_the_chat_is_paused()
            : m.chat_the_chat_pauses_when_the_budget_runs_out()}
        </p>
      </section>
      {turns.isPending ? (
        <LoadingRows rows={2} />
      ) : turns.isError ? (
        <ErrorState error={turns.error} onRetry={() => void turns.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{m.chat_no_turns_yet()}</p>
      ) : (
        <>
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <Stat label={m.chat_turns()} value={count(items.length)} />
            <Stat label={m.chat_steps()} value={count(total.steps)} />
            <Stat label={m.chat_time()} value={seconds(total.time)} />
            <div className="bg-muted/50 col-span-3 rounded-lg px-3 py-2">
              <dt className="text-muted-foreground text-xs">
                {m.chat_tokens()}
              </dt>
              <dd className="font-medium tabular-nums">
                {m.chat_in_out({
                  in: count(total.input),
                  out: count(total.output),
                })}
              </dd>
            </div>
          </dl>
          <section aria-labelledby="turns-heading">
            <Heading>
              <span id="turns-heading">
                {m.chat_turn({ count: items.length })}
              </span>
            </Heading>
            <ol className="divide-y">
              {[...items].reverse().map((turn) => (
                <li
                  className="flex items-center gap-3 py-2 text-sm"
                  key={turn.id}
                >
                  <div className="min-w-0 flex-1">
                    <StatusText tone={OUTCOME[turn.status].tone}>
                      {OUTCOME[turn.status].label()}
                    </StatusText>
                    <p className="text-muted-foreground truncate text-xs tabular-nums">
                      {m.chat_turn_meta({
                        date: dateTime(turn.started_at),
                        duration: between(turn.started_at, turn.finished_at),
                        steps: m.chat_step({ count: turn.steps }),
                        tokens: m.chat_token({
                          count: turn.input_tokens + turn.output_tokens,
                        }),
                      })}
                    </p>
                  </div>
                  <span className="shrink-0 tabular-nums">
                    {usd(turn.cost_usd)}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </div>
  );
}
