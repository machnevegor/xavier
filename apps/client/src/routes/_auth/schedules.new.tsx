import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { flatten } from "@/api/query.ts";
import type { ChatTiming } from "@/api/types.ts";
import { FormField } from "@/components/form-field.tsx";
import { Breadcrumbs, Page, PageHeader } from "@/components/page.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import {
  ChatOptions,
  type ChatOptionsValue,
  DEFAULT_OPTIONS,
  ModeToggle,
  stepLimitReady,
  toChatSettings,
} from "@/features/chats/chat-options.tsx";
import { ACTIVE_STATUSES } from "@/features/chats/model.ts";
import { createSchedule } from "@/features/schedules/api.ts";
import { nextCronRuns } from "@/features/schedules/cron.ts";
import {
  buildTiming,
  emptyTiming,
  type TimingForm,
} from "@/features/schedules/timing.ts";
import { TimingEditor } from "@/features/schedules/timing-editor.tsx";
import { dateTime } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const NEW_CHAT = "new";
const SCHEDULED_OPTIONS: ChatOptionsValue = {
  ...DEFAULT_OPTIONS,
  tool_groups: ["observe"],
};

export const Route = createFileRoute("/_auth/schedules/new")({
  component: NewSchedulePage,
  head: () => ({
    meta: [{ title: m.schedule_new_new_schedule_schedules_xavier() }],
  }),
});

function NewSchedulePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [target, setTarget] = useState(NEW_CHAT);
  const [options, setOptions] = useState(SCHEDULED_OPTIONS);
  const [timingForm, setTimingForm] = useState(emptyTiming);
  const [attempted, setAttempted] = useState(false);
  const chats = useInfiniteQuery(chatQueries.list(ACTIVE_STATUSES));
  const targets = [
    { label: m.schedule_new_a_new_chat_for_every_run(), value: NEW_CHAT },
    ...flatten(chats.data).map((chat) => ({
      label: chat.title,
      value: chat.id,
    })),
  ];

  const create = useMutation({
    mutationFn: ({
      timing,
    }: {
      timing: ChatTiming;
      predicted: Date | null;
    }) => {
      const {
        proactive: _proactive,
        max_steps: steps,
        ...settings
      } = toChatSettings(options);
      return createSchedule({
        name: name.trim(),
        paused: false,
        text: text.trim(),
        timing,
        ...settings,
        ...(target === NEW_CHAT ? { max_steps: steps } : {}),
        chat_id: target === NEW_CHAT ? null : target,
      });
    },
    onSuccess: async (schedule, { predicted }) => {
      await queryClient.invalidateQueries({ queryKey: ["schedules"] });
      // Xavier's scheduler is the authority. If its first run is not the one previewed, say so.
      const planned = schedule.next_runs[0];
      if (
        predicted &&
        planned &&
        Math.abs(new Date(planned).getTime() - predicted.getTime()) >= 60_000
      ) {
        toast.add({
          description:
            m.schedule_new_preview_xavier_xaviers_time_is_the_one_that({
              a: dateTime(predicted.toISOString()),
              b: dateTime(planned),
            }),
          timeout: 0,
          title: m.schedule_new_the_first_run_differs_from_the_preview(),
          type: "warning",
        });
      }
      await navigate({ to: "/schedules" });
    },
  });

  const built = buildTiming(timingForm);
  // Typed values are checked as you type; choices only once you try to save.
  const typed =
    timingForm.kind === "cron" ||
    (timingForm.kind === "repeat" && timingForm.every !== "week");
  const timingError = attempted || typed ? built.error : undefined;
  const fresh = target === NEW_CHAT;
  const noTools = fresh && options.tool_groups.length === 0;
  const stepsReady = !fresh || stepLimitReady(options);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    if (!built.timing || noTools || !stepsReady) {
      return;
    }
    const { cron, time_zone: zone } = built.timing;
    create.mutate({
      predicted: cron && zone ? (nextCronRuns(cron, zone, 1)[0] ?? null) : null,
      timing: built.timing,
    });
  };

  return (
    <Page className="max-w-3xl space-y-6">
      <Breadcrumbs
        items={[
          { label: m.schedule_new_schedules(), to: "/schedules" },
          { label: m.schedule_new_new_schedule() },
        ]}
      />
      <PageHeader
        lead={m.schedule_new_at_the_chosen_time_xavier_posts_your_message()}
        title={m.schedule_new_schedule_a_task()}
      />
      <form className="space-y-6" onSubmit={submit}>
        <FormField id="schedule-name" label={m.schedule_new_name()}>
          <Input
            id="schedule-name"
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
            required
            value={name}
          />
        </FormField>
        <FormField
          id="schedule-text"
          label={m.schedule_new_message_to_the_agent()}
        >
          <Textarea
            id="schedule-text"
            maxLength={20_000}
            onChange={(event) => setText(event.target.value)}
            required
            rows={4}
            value={text}
          />
        </FormField>
        <TimingEditor
          error={timingError}
          form={timingForm}
          set={(patch: Partial<TimingForm>) =>
            setTimingForm((current) => ({ ...current, ...patch }))
          }
        />
        <FormField
          help={
            fresh
              ? m.schedule_new_each_run_starts_a_fresh_chat_with_the()
              : m.schedule_new_each_run_posts_into_this_chat_with_its()
          }
          id="schedule-target"
          label={m.schedule_new_where_it_posts()}
        >
          <Select
            disabled={chats.isPending || chats.isError}
            items={targets}
            onValueChange={(next) => setTarget(next ?? NEW_CHAT)}
            value={target}
          >
            <SelectTrigger className="w-full" id="schedule-target">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {targets.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {chats.isPending && (
            <p className="text-muted-foreground text-sm" role="status">
              {m.schedule_new_loading_chats()}
            </p>
          )}
          {chats.isError && (
            <ErrorState
              error={chats.error}
              onRetry={() => void chats.refetch()}
            />
          )}
        </FormField>
        {fresh && (
          <div className="space-y-5">
            <ModeToggle
              describedBy="schedule-mode"
              onChange={(mode) => setOptions({ ...options, mode })}
              value={options.mode}
            />
            <ChatOptions onChange={setOptions} value={options} />
          </div>
        )}
        {noTools && attempted && (
          <p className="text-attention">
            {m.schedule_new_choose_at_least_one_thing_the_agent_may()}
          </p>
        )}
        {create.isError && <ErrorState error={create.error} />}
        <div className="flex flex-wrap gap-2">
          <Button disabled={create.isPending || !stepsReady} type="submit">
            {create.isPending
              ? m.schedule_new_saving()
              : m.schedule_new_save_schedule()}
          </Button>
          <Button
            nativeButton={false}
            render={<Link to="/schedules" />}
            variant="ghost"
          >
            {m.schedule_new_cancel()}
          </Button>
        </div>
      </form>
    </Page>
  );
}
