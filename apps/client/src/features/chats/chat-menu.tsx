import {
  IconDotsVertical,
  IconPencil,
  IconPlayerPause,
  IconPlayerPlay,
  IconPlayerStop,
  IconSettings,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { explain } from "@/api/errors.ts";
import type { Chat } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { type ChatCommand, runChatCommand } from "@/features/chats/api.ts";
import { canStop } from "@/features/chats/model.ts";
import { m } from "@/paraglide/messages.js";

/** Stop, pause, resume and delete; a failure stays on screen until dismissed. */
export function useChatCommand(chat: Chat) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: ChatCommand) => runChatCommand(chat.id, name),
    onError: (error) => {
      const { title, hint } = explain(error);
      toast.add({ description: hint, timeout: 0, title, type: "error" });
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          (queryKey[0] === "chat" && queryKey[1] === chat.id) ||
          queryKey[0] === "chats" ||
          queryKey[0] === "approvals",
      }),
    onSuccess: async (_, name) => {
      if (name === "delete") {
        await navigate({ to: "/" });
      }
    },
  });
}

/**
 * Everything one can do with the chat, in one place: setup first, then the commands its state
 * allows, and deleting last.
 */
export function ChatMenu({
  chat,
  onSettings,
  onRename,
}: {
  chat: Chat;
  onSettings: () => void;
  onRename: () => void;
}) {
  const command = useChatCommand(chat);
  const [deleting, setDeleting] = useState(false);
  const busy = command.isPending;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              aria-label={m.chat_chat_actions()}
              className="text-muted-foreground"
              size="icon"
              variant="ghost"
            />
          }
        >
          <IconDotsVertical />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={onSettings}>
              <IconSettings aria-hidden="true" />
              {m.chat_settings()}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onRename}>
              <IconPencil aria-hidden="true" />
              {m.chat_rename()}
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            {canStop(chat) && (
              <DropdownMenuItem
                disabled={busy}
                onClick={() => command.mutate("stop")}
              >
                <IconPlayerStop aria-hidden="true" />
                {m.chat_stop_2()}
              </DropdownMenuItem>
            )}
            {chat.status === "paused" ? (
              <DropdownMenuItem
                disabled={busy}
                onClick={() => command.mutate("resume")}
              >
                <IconPlayerPlay aria-hidden="true" />
                {m.chat_resume_2()}
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem
                disabled={busy}
                onClick={() => command.mutate("pause")}
              >
                <IconPlayerPause aria-hidden="true" />
                {m.chat_pause()}
              </DropdownMenuItem>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={busy}
            onClick={() => setDeleting(true)}
            variant="destructive"
          >
            <IconTrash aria-hidden="true" />
            {m.chat_delete()}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmAction
        confirmLabel={m.chat_delete_chat()}
        description={m.chat_the_agent_stops_and_the_chat_is_removed()}
        onConfirm={() => command.mutate("delete")}
        onOpenChange={setDeleting}
        open={deleting}
        title={m.chat_delete_this_chat()}
      />
    </>
  );
}
