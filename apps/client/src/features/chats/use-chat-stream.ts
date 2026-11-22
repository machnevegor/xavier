import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { openChatStream, type StreamState } from "@/api/stream.ts";
import type { ChatEvent, Page } from "@/api/types.ts";
import { chatKeys, chatQueries } from "@/features/chats/api.ts";

const REFRESH_DELAY_MS = 120;

/**
 * Keep one chat live. Events are appended to the cached log, and each burst of events
 * refreshes the typed resources (chat, messages, turns, actions) the screens render from, so
 * the UI never infers state from an event payload.
 */
export function useChatStream(chatId: string): StreamState {
  const queryClient = useQueryClient();
  const events = useQuery(chatQueries.events(chatId));
  const [state, setState] = useState<StreamState>("connecting");
  const ready = events.isSuccess;

  useEffect(() => {
    if (!ready) {
      return;
    }
    const key = chatKeys.events(chatId);
    const cursor =
      queryClient.getQueryData<Page<ChatEvent>>(key)?.items.at(-1)?.id ?? null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const close = openChatStream(chatId, {
      cursor,
      onEvent: (event) => {
        queryClient.setQueryData<Page<ChatEvent>>(key, (page) =>
          !page || page.items.some((known) => known.id === event.id)
            ? page
            : { ...page, items: [...page.items, event] },
        );
        clearTimeout(timer);
        timer = setTimeout(() => {
          void queryClient.invalidateQueries({
            predicate: ({ queryKey }) =>
              (queryKey[0] === "chat" &&
                queryKey[1] === chatId &&
                queryKey[2] !== "events") ||
              queryKey[0] === "chats" ||
              queryKey[0] === "approvals",
          });
        }, REFRESH_DELAY_MS);
      },
      onState: setState,
    });
    return () => {
      clearTimeout(timer);
      close();
    };
  }, [chatId, ready, queryClient]);

  return events.isError ? "failed" : state;
}
