import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { chatQueries } from "@/features/chats/api.ts";
import { ChatFeed } from "@/features/chats/feed/chat-feed.tsx";
import { useBottomFollower } from "@/features/chats/feed/follow-bottom.ts";
import { ScrollToLatest } from "@/features/chats/feed/scroll-to-latest.tsx";
import { MessageComposer } from "@/features/chats/message-composer.tsx";

export const Route = createFileRoute("/_auth/chats/$chatId/")({
  component: ConversationPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { message?: string; event?: string } => ({
    event: typeof search.event === "string" ? search.event : undefined,
    message: typeof search.message === "string" ? search.message : undefined,
  }),
});

function ConversationPage() {
  const { chatId } = Route.useParams();
  const { message, event } = Route.useSearch();
  const { data: chat } = useQuery(chatQueries.detail(chatId));
  const follower = useBottomFollower(chatId);
  if (!chat) {
    return null;
  }
  return (
    <div
      className="mx-auto flex w-full max-w-3xl flex-1 flex-col"
      ref={follower.observe}
    >
      <div className="flex-1 py-6">
        <ChatFeed
          chat={chat}
          highlightEventId={event}
          highlightMessageId={message}
          onHighlight={follower.release}
        />
      </div>
      <MessageComposer
        above={
          <ScrollToLatest chatId={chatId} follower={follower} key={chatId} />
        }
        chat={chat}
      />
    </div>
  );
}
