import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { Chat } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog.tsx";
import { Input } from "@/components/ui/input.tsx";
import { chatKeys, patchChat } from "@/features/chats/api.ts";
import { m } from "@/paraglide/messages.js";

/** Renames the chat in place; a title the operator sets is never overwritten by the default. */
export function ChatRename({
  chat,
  open,
  onOpenChange,
}: {
  chat: Chat;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const fieldId = useId();
  const [title, setTitle] = useState(chat.title);
  const patch = useMutation({
    mutationFn: () => patchChat(chat.id, { title: title.trim() }),
    onSuccess: (saved) => {
      queryClient.setQueryData(chatKeys.chat(chat.id), saved);
      void queryClient.invalidateQueries({ queryKey: chatKeys.all });
      onOpenChange(false);
    },
  });

  // The field starts from the chat as it is now each time the dialog opens.
  const [opened, setOpened] = useState(open);
  if (open !== opened) {
    setOpened(open);
    if (open) {
      setTitle(chat.title);
      patch.reset();
    }
  }

  const canSave = title.trim() !== "" && title.trim() !== chat.title;

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{m.chat_rename_chat()}</DialogTitle>
          <DialogDescription>
            {m.chat_xavier_wont_replace_your_new_title_later()}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSave) {
              patch.mutate();
            }
          }}
        >
          <div>
            <label className="sr-only" htmlFor={fieldId}>
              {m.chat_title()}
            </label>
            <Input
              autoFocus
              id={fieldId}
              maxLength={200}
              onChange={(event) => setTitle(event.target.value)}
              required
              value={title}
            />
          </div>
          {patch.isError && <ErrorState error={patch.error} />}
          <DialogFooter>
            <Button
              onClick={() => onOpenChange(false)}
              type="button"
              variant="outline"
            >
              {m.chat_cancel()}
            </Button>
            <Button disabled={!canSave || patch.isPending} type="submit">
              {patch.isPending ? m.chat_saving() : m.chat_save()}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
