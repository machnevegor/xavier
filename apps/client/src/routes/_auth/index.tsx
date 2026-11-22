import { useInfiniteQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { flatten } from "@/api/query.ts";
import type { Chat } from "@/api/types.ts";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadMore,
} from "@/components/states.tsx";
import { Status } from "@/components/status-text.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import { ACTIVE_STATUSES, chatStatus } from "@/features/chats/model.ts";
import { NewChatForm } from "@/features/chats/new-chat-form.tsx";
import { timeAgo, usd } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/")({
  component: WorkPage,
  head: () => ({ meta: [{ title: m.work_work_xavier() }] }),
});

function WorkPage() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 lg:px-10">
      <h1 className="type-title">{m.work_what_can_i_help_you_tackle()}</h1>
      <p className="text-muted-foreground mt-1 mb-5">
        {m.work_research_with_sources_watch_a_page_analyze_a()}
      </p>
      <NewChatForm />

      <section aria-labelledby="chats-heading" className="mt-12">
        <h2 className="type-section" id="chats-heading">
          {m.work_your_work()}
        </h2>
        <div className="mt-4">
          <ChatList />
        </div>
      </section>
    </div>
  );
}

function ChatList() {
  const query = useInfiniteQuery(chatQueries.list(ACTIVE_STATUSES));
  if (query.isPending) {
    return <LoadingRows />;
  }
  if (query.isError) {
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }
  const chats = flatten(query.data);
  if (chats.length === 0) {
    return (
      <EmptyState title={m.work_no_work_yet()}>
        {m.work_describe_a_task_above_and_it_will_appear()}
      </EmptyState>
    );
  }
  return (
    <>
      <ul className="divide-y border-y">
        {chats.map((chat) => (
          <ChatRow chat={chat} key={chat.id} />
        ))}
      </ul>
      <LoadMore {...query} />
    </>
  );
}

function ChatRow({ chat }: { chat: Chat }) {
  return (
    <li>
      <Link
        className="hover:bg-accent/50 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3.5"
        params={{ chatId: chat.id }}
        to="/chats/$chatId"
      >
        <span className="min-w-0 flex-1 basis-56 truncate font-medium">
          {chat.title}
        </span>
        {chat.computer_active && (
          <span className="text-muted-foreground">{m.work_computer_on()}</span>
        )}
        <Status meta={chatStatus(chat)} />
        <span className="text-muted-foreground text-right whitespace-nowrap sm:w-44">
          {usd(chat.spent_usd)} — {timeAgo(chat.updated_at)}
        </span>
      </Link>
    </li>
  );
}
