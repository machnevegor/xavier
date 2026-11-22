import { IconDots, IconMoon, IconTrash } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { Workspace } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { ErrorState } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu.tsx";
import {
  runWorkspaceCommand,
  type WorkspaceCommand,
} from "@/features/workspace/api.ts";
import { bytes, timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const PENDING: Record<WorkspaceCommand, () => string> = {
  delete: m.chat_deleting_the_workspace,
  hibernate: m.chat_saving_a_checkpoint,
  wake: m.chat_waking_the_workspace_and_restoring_files,
};
const REACHED: Record<WorkspaceCommand, Workspace["status"]> = {
  delete: "deleted",
  hibernate: "hibernated",
  wake: "active",
};

/**
 * The workspace in one line, with what can be done to it. Wake, hibernate and delete are
 * accepted first and finish later, so the line keeps saying what was asked until the workspace
 * reports the new state.
 */
export function WorkspacePanel({
  workspace,
  chatId,
}: {
  workspace: Workspace;
  chatId: string;
}) {
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const command = useMutation({
    mutationFn: (name: WorkspaceCommand) => runWorkspaceCommand(chatId, name),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["chat", chatId] }),
  });
  const status = workspace.status;
  const asked =
    command.isSuccess || command.isPending ? command.variables : undefined;
  const inFlight = asked !== undefined && status !== REACHED[asked];
  const exists = status === "active" || status === "hibernated";
  const label = inFlight
    ? PENDING[asked]()
    : status === "active"
      ? m.chat_workspace_2()
      : status === "hibernated"
        ? m.chat_workspace_asleep()
        : status === "deleted"
          ? m.chat_workspace_deleted()
          : m.chat_no_workspace_yet();

  return (
    <section aria-label={m.chat_workspace_2()} className="space-y-2">
      <div className="flex min-h-9 items-center gap-2">
        <div className="min-w-0 flex-1">
          <StatusText
            className="font-medium"
            pulse={inFlight}
            tone={inFlight || status === "active" ? "live" : "neutral"}
          >
            {label}
          </StatusText>
          {exists && !inFlight && (
            <p className="text-muted-foreground text-xs">
              {m.chat_saved({
                size: bytes(workspace.size_bytes),
                when: workspace.checkpoint_at
                  ? timeAgo(workspace.checkpoint_at)
                  : m.format_never(),
              })}
            </p>
          )}
        </div>
        {status === "hibernated" && (
          <Button
            disabled={inFlight}
            onClick={() => command.mutate("wake")}
            size="sm"
          >
            {m.chat_wake()}
          </Button>
        )}
        {exists && (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  aria-label={m.chat_workspace_actions()}
                  className="text-muted-foreground"
                  disabled={inFlight}
                  size="icon-sm"
                  variant="ghost"
                />
              }
            >
              <IconDots />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              {status === "active" && (
                <>
                  <DropdownMenuItem onClick={() => command.mutate("hibernate")}>
                    <IconMoon aria-hidden="true" />
                    {m.chat_hibernate_now()}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuItem
                onClick={() => setDeleting(true)}
                variant="destructive"
              >
                <IconTrash aria-hidden="true" />
                {m.chat_delete_workspace()}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {status === "hibernated" && !inFlight && (
        <p className="text-muted-foreground text-sm">
          {m.chat_files_are_kept_in_a_checkpoint_wake_it()}
        </p>
      )}
      {command.isError && (
        <ErrorState
          error={command.error}
          onRetry={() => {
            if (command.variables) {
              command.mutate(command.variables);
            }
          }}
        />
      )}
      <ConfirmAction
        confirmLabel={m.chat_delete_all_workspace_data()}
        description={m.chat_this_permanently_deletes_every_file_in_workspace_the()}
        onConfirm={() => command.mutate("delete")}
        onOpenChange={setDeleting}
        open={deleting}
        title={m.chat_delete_this_workspace_and_its_data()}
      />
    </section>
  );
}
