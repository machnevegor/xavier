import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import type { Schedule } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { Page, PageHeader } from "@/components/page.tsx";
import { EmptyState, ErrorState, QueryBoundary } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { chatKeys } from "@/features/chats/api.ts";
import {
  deleteSchedule,
  runScheduleCommand,
  type ScheduleCommand,
  scheduleQueries,
} from "@/features/schedules/api.ts";
import { describeTiming } from "@/features/schedules/timing.ts";
import { timeAgo, timeUntil } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/schedules/")({
  component: SchedulesPage,
  head: () => ({ meta: [{ title: m.schedules_schedules_xavier() }] }),
});

function nextText(schedule: Schedule): string {
  if (schedule.paused) {
    return m.schedules_paused();
  }
  const [next] = schedule.next_runs;
  if (next) {
    return m.schedules_next({ in: timeUntil(next) });
  }
  return schedule.timing.at
    ? m.schedules_already_ran()
    : m.schedules_no_upcoming_run();
}

function SchedulesPage() {
  const queryClient = useQueryClient();
  const schedules = useQuery(scheduleQueries.list());
  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["schedules"] }),
      queryClient.invalidateQueries({ queryKey: chatKeys.all }),
    ]);
  const command = useMutation({
    mutationFn: ({ id, name }: { id: string; name: ScheduleCommand }) =>
      runScheduleCommand(id, name),
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: deleteSchedule,
    onSettled: invalidate,
  });
  const busy = command.isPending || remove.isPending;

  return (
    <Page>
      <PageHeader
        lead={m.schedules_work_that_starts_on_its_own_a_message()}
        title={m.schedules_schedules()}
      >
        <Button nativeButton={false} render={<Link to="/schedules/new" />}>
          {m.schedules_schedule_a_task()}
        </Button>
      </PageHeader>
      {(command.isError || remove.isError) && (
        <ErrorState error={command.error ?? remove.error} />
      )}
      {command.isSuccess && command.variables.name === "trigger" && (
        <p className="text-muted-foreground" role="status">
          {m.schedules_run_requested_the_message_appears_in_its_chat()}
        </p>
      )}
      <QueryBoundary query={schedules}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title={m.schedules_nothing_is_scheduled()}>
              {m.schedules_schedule_a_task_to_have_the_agent_start()}
            </EmptyState>
          ) : (
            <ul className="divide-y border-y">
              {items.map((schedule) => (
                <li
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 py-4"
                  key={schedule.id}
                >
                  <div className="min-w-0 flex-1 basis-72">
                    <p className="font-medium">{schedule.name}</p>
                    <p className="text-muted-foreground mt-0.5 line-clamp-2">
                      “{schedule.text}”
                    </p>
                    <p className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 text-sm">
                      <span>{describeTiming(schedule.timing)}</span>
                      <StatusText
                        tone={
                          !schedule.paused && schedule.next_runs.length
                            ? "live"
                            : "neutral"
                        }
                      >
                        {nextText(schedule)}
                      </StatusText>
                      {schedule.recent_runs[0] && (
                        <span>
                          {m.schedules_last_ran({
                            ago: timeAgo(schedule.recent_runs[0]),
                          })}
                        </span>
                      )}
                      {schedule.chat_id ? (
                        <Link
                          className="underline underline-offset-2"
                          params={{ chatId: schedule.chat_id }}
                          to="/chats/$chatId"
                        >
                          {m.schedules_posts_into_a_chat()}
                        </Link>
                      ) : (
                        <span>{m.schedules_new_chat_each_run()}</span>
                      )}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        command.mutate({ id: schedule.id, name: "trigger" })
                      }
                      size="sm"
                      variant="outline"
                    >
                      {m.schedules_run_now()}
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        command.mutate({
                          id: schedule.id,
                          name: schedule.paused ? "resume" : "pause",
                        })
                      }
                      size="sm"
                      variant="outline"
                    >
                      {schedule.paused
                        ? m.schedules_resume()
                        : m.schedules_pause()}
                    </Button>
                    <ConfirmAction
                      confirmLabel={m.schedules_delete_schedule()}
                      description={m.schedules_no_further_runs_will_start_chats_it_already()}
                      onConfirm={() => remove.mutate(schedule.id)}
                      title={m.schedules_delete_2({ name: schedule.name })}
                      trigger={
                        <Button disabled={busy} size="sm" variant="ghost">
                          {m.schedules_delete()}
                        </Button>
                      }
                    />
                  </div>
                </li>
              ))}
            </ul>
          )
        }
      </QueryBoundary>
    </Page>
  );
}
