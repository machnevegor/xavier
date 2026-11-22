import { IconArrowUp, IconPlayerStopFilled } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import type { Chat, ChatStatus } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Select,
  SelectContent,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import {
  AttachButton,
  AttachmentList,
  FileDropZone,
  pasteFiles,
} from "@/features/artifacts/attachment-picker.tsx";
import { useAttachments } from "@/features/artifacts/use-attachments.ts";
import { catalogQueries } from "@/features/catalog/api.ts";
import { useReportComposing } from "@/features/chats/agent-avatar.tsx";
import { chatKeys, patchChat, sendMessage } from "@/features/chats/api.ts";
import { useChatCommand } from "@/features/chats/chat-menu.tsx";
import { ModelItems, selectedModel } from "@/features/chats/chat-options.tsx";
import { useAutoGrow } from "@/hooks/use-auto-grow.ts";
import { m } from "@/paraglide/messages.js";

const WAITS: Partial<Record<ChatStatus, () => string>> = {
  needs_approval: m.chat_the_agent_is_waiting_for_your_decision_your,
  paused: m.chat_this_chat_is_paused_your_messages_will_queue,
  working: m.chat_a_new_message_interrupts_the_agent_and_it,
};

export function MessageComposer({
  chat,
  above,
}: {
  chat: Chat;
  /** Floats at the end of the line above the composer. */
  above?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const models = useQuery(catalogQueries.models());
  const [text, setText] = useState("");
  const [focused, setFocused] = useState(false);
  const attachments = useAttachments(chat.id, chat.id);
  const field = useAutoGrow();
  useReportComposing(focused || text !== "");
  const seesImages =
    selectedModel(models.data, chat.model)?.input.includes("image") ?? false;
  const note = attachments.note(seesImages, chat.tool_groups);
  const send = useMutation({
    mutationFn: () =>
      sendMessage(chat.id, {
        text: text.trim(),
        ...attachments.references(seesImages),
      }),
    onSuccess: () => {
      setText("");
      attachments.clear();
      void queryClient.invalidateQueries({ queryKey: chatKeys.chat(chat.id) });
    },
  });
  const command = useChatCommand(chat);
  const drafted =
    text.trim() !== "" || attachments.ready > 0 || attachments.uploading;
  const canSend = drafted && !attachments.uploading && !send.isPending;
  const submit = () => {
    if (canSend) {
      send.mutate();
    }
  };
  const stops = !drafted && chat.status === "working";

  return (
    <form
      className="bg-background sticky bottom-(--shell-bottom) pt-2 pb-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {above && (
        <div className="absolute right-2.25 bottom-full mb-2">{above}</div>
      )}
      {send.isError && (
        <div className="mb-2">
          <ErrorState error={send.error} onRetry={submit} />
        </div>
      )}
      <FileDropZone attachments={attachments} className="p-2">
        <AttachmentList attachments={attachments} className="px-1 pb-2" />
        {note && <p className="text-attention px-1 pb-2 text-xs">{note}</p>}
        <div className="flex items-end gap-1">
          <AttachButton attachments={attachments} />
          <label className="sr-only" htmlFor="message">
            {m.chat_message()}
          </label>
          <Textarea
            aria-describedby="message-hint"
            className="max-h-40 min-h-9 min-w-0 resize-none border-0 bg-transparent py-1.5 shadow-none focus-visible:ring-0 md:py-2 dark:bg-transparent"
            id="message"
            onBlur={() => setFocused(false)}
            onChange={(event) => setText(event.target.value)}
            onFocus={() => setFocused(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            onPaste={(event) => pasteFiles(event, attachments)}
            placeholder={m.chat_reply_or_add_an_instruction()}
            ref={field}
            rows={1}
            value={text}
          />
          {stops ? (
            <Button
              aria-label={m.chat_stop_the_agent()}
              className="shrink-0"
              disabled={command.isPending}
              key="stop"
              onClick={() => command.mutate("stop")}
              size="icon"
              title={m.chat_stop_the_agent()}
              type="button"
              variant="secondary"
            >
              <IconPlayerStopFilled className="animate-in fade-in-0 zoom-in-50 duration-150" />
            </Button>
          ) : (
            <Button
              aria-label={
                send.isPending ? m.chat_sending_2() : m.chat_send_message()
              }
              className="shrink-0 transition-opacity"
              disabled={!canSend}
              key="send"
              size="icon"
              type="submit"
            >
              <IconArrowUp className="animate-in fade-in-0 zoom-in-50 duration-150" />
            </Button>
          )}
        </div>
      </FileDropZone>
      <div className="mt-1 flex items-center gap-2 pl-2">
        <p
          className="text-muted-foreground min-w-0 flex-1 text-xs"
          id="message-hint"
        >
          {WAITS[chat.status]?.() ?? m.chat_shift_enter_for_a_new_line()}
        </p>
        <ModelPicker chat={chat} />
      </div>
    </form>
  );
}

/** Switches the chat's model in place; a running turn finishes on the previous one. */
function ModelPicker({ chat }: { chat: Chat }) {
  const queryClient = useQueryClient();
  const models = useQuery(catalogQueries.models());
  const patch = useMutation({
    mutationFn: (model: string) => patchChat(chat.id, { model }),
    onSuccess: (saved) => {
      queryClient.setQueryData(chatKeys.chat(chat.id), saved);
      void queryClient.invalidateQueries({ queryKey: chatKeys.all });
    },
  });
  return (
    <>
      {patch.isError && (
        <span className="text-destructive text-xs" role="alert">
          {m.chat_model_not_changed()}
        </span>
      )}
      <Select
        disabled={!models.data || patch.isPending}
        onValueChange={(model) => {
          if (typeof model === "string" && model !== chat.model) {
            patch.mutate(model);
          }
        }}
        value={chat.model}
      >
        <SelectTrigger
          aria-label={m.chat_model_5()}
          className="text-muted-foreground hover:text-foreground h-7 border-0 bg-transparent px-1.5 text-xs shadow-none dark:bg-transparent dark:hover:bg-transparent"
          size="sm"
          title={m.chat_applies_from_the_next_turn()}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent
          align="end"
          alignItemWithTrigger={false}
          className="w-auto max-w-(--available-width)"
        >
          <ModelItems models={models.data ?? []} />
        </SelectContent>
      </Select>
    </>
  );
}
