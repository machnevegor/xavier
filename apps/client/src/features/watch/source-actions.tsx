import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import type { Source } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  deleteSource,
  runSourceCommand,
  type SourceCommand,
  watchKeys,
} from "@/features/watch/api.ts";
import { m } from "@/paraglide/messages.js";

export function SourceActions({ source }: { source: Source }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: watchKeys.source(source.id) }),
      queryClient.invalidateQueries({ queryKey: watchKeys.sources }),
    ]);
  const command = useMutation({
    mutationFn: (name: SourceCommand) => runSourceCommand(source.id, name),
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => deleteSource(source.id),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: watchKeys.source(source.id) });
      await queryClient.invalidateQueries({ queryKey: watchKeys.sources });
      await queryClient.invalidateQueries({ queryKey: watchKeys.alerts });
      await navigate({ to: "/watch" });
    },
  });
  const busy = command.isPending || remove.isPending;
  const revision = source.draft !== null;
  const pendingCommand = command.isPending ? command.variables : null;

  return (
    <div className="space-y-3">
      {(source.status === "draft" || revision) && (
        <div className="border-attention bg-card rounded-lg border p-4">
          <p className="font-medium">
            {revision
              ? m.watch_source_a_revision_is_waiting_for_your_review()
              : m.watch_source_this_draft_is_not_being_checked_yet()}
          </p>
          <p className="text-muted-foreground mt-1">
            {source.chat_id
              ? `${m.watch_source_the_agent_drafted_these_rules()} `
              : ""}
            {m.watch_source_publishing_starts_checks_on_the_timetable_shown()}
            {source.widening.length > 0
              ? ` ${m.watch_source_and_approves()}`
              : ""}
          </p>
          {source.widening.length > 0 && (
            <ul className="mt-2 list-disc pl-5 font-mono text-sm">
              {source.widening.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          <Button
            className="mt-3"
            disabled={busy}
            onClick={() => command.mutate("publish")}
          >
            {pendingCommand === "publish"
              ? m.watch_source_publishing()
              : m.watch_source_publish_and_start_watching()}
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {source.status === "published" && (
          <>
            <Button
              disabled={busy}
              onClick={() => command.mutate("check")}
              size="sm"
              variant="outline"
            >
              {pendingCommand === "check"
                ? m.watch_source_requesting()
                : m.watch_source_check_now()}
            </Button>
            <Button
              disabled={busy}
              onClick={() => command.mutate("pause")}
              size="sm"
              variant="outline"
            >
              {m.watch_source_pause()}
            </Button>
          </>
        )}
        {source.status === "paused" && (
          <Button
            disabled={busy}
            onClick={() => command.mutate("resume")}
            size="sm"
          >
            {m.watch_source_resume()}
          </Button>
        )}
        <ConfirmAction
          confirmLabel={m.watch_source_delete_source_and_history()}
          description={m.watch_source_checks_stop_and_the_recorded_versions_alerts_and()}
          onConfirm={() => remove.mutate()}
          title={m.watch_source_delete_2({ name: source.name })}
          trigger={
            <Button disabled={busy} size="sm" variant="ghost">
              {m.watch_source_delete()}
            </Button>
          }
        />
      </div>
      {command.isSuccess && command.variables === "check" && (
        <p className="text-muted-foreground" role="status">
          {m.watch_source_check_requested_a_new_version_appears_below_only()}
        </p>
      )}
      {(command.isError || remove.isError) && (
        <ErrorState error={command.error ?? remove.error} />
      )}
    </div>
  );
}
