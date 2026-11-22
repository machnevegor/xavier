import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { allPages, numberedPages, pagesOptions } from "@/api/query.ts";
import type {
  ChatCreate,
  ChatPatch,
  ChatStatus,
  MessageInput,
} from "@/api/types.ts";

/** The largest page the API serves. */
const PAGE = 200;

export type ChatCommand = "pause" | "resume" | "stop" | "delete";

const path = (chatId: string) => ({ path: { chat_id: chatId } });

export const chatKeys = {
  all: ["chats"] as const,
  chat: (chatId: string) => ["chat", chatId] as const,
  events: (chatId: string) => ["chat", chatId, "events"] as const,
};

/**
 * The conversation's resources are listed oldest first, so the feed reads them to the end;
 * stopping at one page would hide the newest entries of a long chat.
 */
export const chatQueries = {
  actions: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/chats/{chat_id}/actions", {
              params: { ...path(chatId), query: { cursor, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: [...chatKeys.chat(chatId), "actions"],
    }),
  detail: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/chats/{chat_id}", { params: path(chatId), signal }),
        ),
      queryKey: chatKeys.chat(chatId),
      refetchInterval: 15_000,
    }),
  /** The first page; the stream resumes after its last event and delivers the rest. */
  events: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        readPage(
          api.GET("/api/v1/chats/{chat_id}/events", {
            params: { ...path(chatId), query: { per_page: PAGE } },
            signal,
          }),
        ),
      queryKey: chatKeys.events(chatId),
      staleTime: Number.POSITIVE_INFINITY,
    }),
  list: (statuses: readonly ChatStatus[]) => ({
    ...pagesOptions([...chatKeys.all, "list", statuses], (cursor, signal) =>
      readPage(
        api.GET("/api/v1/chats", {
          params: {
            query: {
              cursor,
              "filter[status]": [...statuses],
              per_page: 50,
            },
          },
          signal,
        }),
      ),
    ),
    refetchInterval: 5000,
  }),
  /** Images of the chat, newest first. The list is paged by number; it takes no cursor. */
  media: (chatId: string) =>
    numberedPages([...chatKeys.chat(chatId), "media"], (page, signal) =>
      readPage(
        api.GET("/api/v1/chats/{chat_id}/media", {
          params: { ...path(chatId), query: { page, per_page: 60 } },
          signal,
        }),
      ),
    ),
  messages: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/chats/{chat_id}/messages", {
              params: { ...path(chatId), query: { cursor, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: [...chatKeys.chat(chatId), "messages"],
    }),
  questions: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/chats/{chat_id}/questions", {
              params: { ...path(chatId), query: { cursor, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: [...chatKeys.chat(chatId), "questions"],
      refetchInterval: 5000,
    }),
  turns: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        allPages((cursor) =>
          readPage(
            api.GET("/api/v1/chats/{chat_id}/turns", {
              params: { ...path(chatId), query: { cursor, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: [...chatKeys.chat(chatId), "turns"],
    }),
};

export const createChat = (body: ChatCreate) =>
  read(api.POST("/api/v1/chats", { body: { data: body } }));

export const patchChat = (chatId: string, body: ChatPatch) =>
  read(
    api.PATCH("/api/v1/chats/{chat_id}", {
      body: { data: body },
      params: path(chatId),
    }),
  );

export const sendMessage = (chatId: string, body: MessageInput) =>
  read(
    api.POST("/api/v1/chats/{chat_id}/messages", {
      body: { data: body },
      params: path(chatId),
    }),
  );

export function runChatCommand(chatId: string, command: ChatCommand) {
  const params = path(chatId);
  switch (command) {
    case "pause":
      return read(api.POST("/api/v1/chats/{chat_id}/pause", { params }));
    case "resume":
      return read(api.POST("/api/v1/chats/{chat_id}/resume", { params }));
    case "stop":
      return read(api.POST("/api/v1/chats/{chat_id}/stop", { params }));
    case "delete":
      return read(api.DELETE("/api/v1/chats/{chat_id}", { params }));
  }
}

export function answerQuestion(
  questionId: string,
  answers: { id: string; selected: string[]; custom?: string | null }[],
) {
  return read(
    api.POST("/api/v1/questions/{question_id}/answer", {
      body: { data: answers },
      params: { path: { question_id: questionId } },
    }),
  );
}

export function reviseMessage(
  chatId: string,
  messageId: string,
  body: MessageInput,
) {
  return read(
    api.POST("/api/v1/chats/{chat_id}/messages/{message_id}/revisions", {
      body: { data: body },
      params: { path: { chat_id: chatId, message_id: messageId } },
    }),
  );
}

export function regenerateMessage(chatId: string, messageId: string) {
  return read(
    api.POST("/api/v1/chats/{chat_id}/messages/{message_id}/regenerate", {
      params: { path: { chat_id: chatId, message_id: messageId } },
    }),
  );
}
