import { queryOptions } from "@tanstack/react-query";
import { api, read } from "@/api/client.ts";

export type WorkspaceCommand = "wake" | "hibernate" | "delete";

const path = (chatId: string) => ({ path: { chat_id: chatId } });

export const WORKSPACE_ROOT = "/workspace";

export const workspaceQueries = {
  detail: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/chats/{chat_id}/workspace", {
            params: path(chatId),
            signal,
          }),
        ),
      queryKey: ["chat", chatId, "workspace"],
      refetchInterval: 4000,
    }),
  files: (chatId: string, dir: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/chats/{chat_id}/workspace/files", {
            params: { ...path(chatId), query: { path: dir } },
            signal,
          }),
        ),
      queryKey: ["chat", chatId, "files", dir],
    }),
  computerStatus: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/chats/{chat_id}/computer/status", {
            params: path(chatId),
            signal,
          }),
        ),
      queryKey: ["chat", chatId, "computer-status"],
      refetchInterval: 5000,
    }),
};

/** Each is accepted first and lands a moment later, when the workspace reports it. */
export function runWorkspaceCommand(chatId: string, command: WorkspaceCommand) {
  const params = path(chatId);
  switch (command) {
    case "wake":
      return read(
        api.POST("/api/v1/chats/{chat_id}/workspace/wake", { params }),
      );
    case "hibernate":
      return read(
        api.POST("/api/v1/chats/{chat_id}/workspace/hibernate", { params }),
      );
    case "delete":
      return read(api.DELETE("/api/v1/chats/{chat_id}/workspace", { params }));
  }
}

/** Same-origin, so the session cookie authorises it; the server names the file. */
export const fileUrl = (chatId: string, file: string) =>
  `/api/v1/chats/${encodeURIComponent(chatId)}/workspace/files/content?${new URLSearchParams({ path: file })}`;

export const setControl = (chatId: string, action: "take" | "release") =>
  read(
    api.POST("/api/v1/chats/{chat_id}/computer/control", {
      body: { data: { action } },
      params: path(chatId),
    }),
  );
