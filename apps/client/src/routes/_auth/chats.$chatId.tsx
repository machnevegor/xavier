import { useQuery } from "@tanstack/react-query";
import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  AgentDocumentState,
  ComposingContext,
} from "@/features/chats/agent-avatar.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import { ChatHeader, ChatProfile } from "@/features/chats/chat-header.tsx";
import { ChatRename } from "@/features/chats/chat-rename.tsx";
import { ChatSettings } from "@/features/chats/chat-settings.tsx";
import {
  columnSearch,
  consumeScroll,
  rememberScroll,
} from "@/features/chats/column.ts";
import type { ProfileTab } from "@/features/chats/model.ts";
import { useChatStream } from "@/features/chats/use-chat-stream.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/chats/$chatId")({
  component: ChatLayout,
  head: () => ({ meta: [{ title: m.chat_chat_xavier() }] }),
  loader: ({ context, params }) =>
    context.queryClient.prefetchQuery(chatQueries.detail(params.chatId)),
  validateSearch: columnSearch,
});

function ChatLayout() {
  const { chatId } = Route.useParams();
  const { view, tab } = Route.useSearch();
  const navigate = useNavigate();
  const router = useRouter();
  const chat = useQuery(chatQueries.detail(chatId));
  const stream = useChatStream(chatId);
  const [composing, setComposing] = useState(false);
  const [rename, setRename] = useState(false);
  const pushed = useRef(0);
  const restore = useRef<number | null>(null);

  useLayoutEffect(() => {
    const onPop = () => {
      pushed.current = Math.max(0, pushed.current - 1);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useLayoutEffect(() => {
    if (!view) {
      return;
    }
    const saved = consumeScroll();
    if (saved != null) {
      restore.current = saved;
    }
  }, [view]);

  useEffect(() => {
    if (view) {
      window.scrollTo(0, 0);
      return;
    }
    if (restore.current != null) {
      window.scrollTo(0, restore.current);
      restore.current = null;
    }
  }, [view]);

  const open = (next: "profile" | "settings", nextTab?: ProfileTab) => {
    const push = view !== next;
    if (!view) {
      rememberScroll();
    }
    if (push) {
      pushed.current += 1;
    }
    void navigate({
      replace: !push,
      resetScroll: true,
      search: (previous) => ({
        ...previous,
        tab: next === "profile" ? nextTab : undefined,
        view: next,
      }),
      to: ".",
    });
  };
  const back = () => {
    if (pushed.current > 0) {
      router.history.back();
      return;
    }
    void navigate({
      replace: true,
      resetScroll: false,
      search: (previous) => ({ ...previous, tab: undefined, view: undefined }),
      to: ".",
    });
  };

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 sm:px-8 lg:px-10">
      {chat.data ? (
        <ComposingContext value={setComposing}>
          <AgentDocumentState
            chat={chat.data}
            composing={composing}
            stream={stream}
          />
          <div
            className={view ? "hidden" : "flex flex-1 flex-col"}
            hidden={view ? true : undefined}
          >
            <ChatHeader
              chat={chat.data}
              composing={composing}
              onOpenProfile={(next) => open("profile", next)}
              onOpenSettings={() => open("settings")}
              onRename={() => setRename(true)}
              stream={stream}
            />
            <Outlet />
          </div>
          {view === "profile" && (
            <ChatProfile
              chat={chat.data}
              composing={composing}
              onBack={back}
              onRename={() => setRename(true)}
              onSettings={() => open("settings")}
              onTab={(next) => open("profile", next)}
              stream={stream}
              tab={tab}
            />
          )}
          {view === "settings" && (
            <ChatSettings chat={chat.data} onBack={back} />
          )}
          <ChatRename chat={chat.data} onOpenChange={setRename} open={rename} />
        </ComposingContext>
      ) : chat.isError ? (
        <div className="pt-8">
          <ErrorState error={chat.error} onRetry={() => void chat.refetch()}>
            <Button
              nativeButton={false}
              render={<Link to="/" />}
              size="sm"
              variant="outline"
            >
              {m.chat_back_to_work()}
            </Button>
          </ErrorState>
        </div>
      ) : (
        <div className="pt-8">
          <LoadingRows rows={4} />
        </div>
      )}
    </div>
  );
}
