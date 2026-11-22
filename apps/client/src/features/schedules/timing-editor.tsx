import { useId } from "react";
import { FormField } from "@/components/form-field.tsx";
import { FieldLegend, FieldSet } from "@/components/ui/field.tsx";
import { Input } from "@/components/ui/input.tsx";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Toggle } from "@/components/ui/toggle.tsx";
import {
  clockChangesNear,
  describeCron,
  isTimeZone,
  nextCronRuns,
  parseCron,
  weekdayShort,
} from "@/features/schedules/cron.ts";
import {
  formCron,
  isTimed,
  REPEATS,
  repeatToCron,
  type TimingForm,
  type TimingKind,
} from "@/features/schedules/timing.ts";
import { m } from "@/paraglide/messages.js";
import { getLocale } from "@/paraglide/runtime.js";

const KINDS: readonly { value: TimingKind; label: () => string }[] = [
  { label: m.timing_once, value: "at" },
  { label: m.timing_repeating, value: "repeat" },
  { label: m.timing_advanced_cron, value: "cron" },
];
const DAY_NUMBERS = ["1", "2", "3", "4", "5", "6", "0"] as const;
const HOUR_VALUES = ["1", "2", "3", "4", "6", "12"] as const;
const MINUTE_VALUES = ["5", "10", "15", "30"] as const;
const MONTH_DAYS = Array.from({ length: 28 }, (_, index) => ({
  label: String(index + 1),
  value: String(index + 1),
}));
const ZONES = ["UTC", ...Intl.supportedValuesOf("timeZone")];

type SetTiming = (patch: Partial<TimingForm>) => void;

function Choice({
  id,
  label,
  help,
  value,
  items,
  onChange,
}: {
  id: string;
  label: string;
  help?: string;
  value: string;
  items: readonly { label: string; value: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <FormField help={help} id={id} label={label}>
      <Select
        items={items}
        onValueChange={(next) => next && onChange(next)}
        value={value}
      >
        <SelectTrigger className="w-full sm:w-72" id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FormField>
  );
}

/** A repeating timetable chosen from ordinary patterns, with no cron to write. */
function RepeatFields({ form, set }: { form: TimingForm; set: SetTiming }) {
  const toggleDay = (day: string, on: boolean) =>
    set({
      days: on ? [...form.days, day] : form.days.filter((item) => item !== day),
    });
  const hours = HOUR_VALUES.map((n) => ({
    label: n === "1" ? m.timing_hour() : m.timing_hours({ count: Number(n) }),
    value: n,
  }));
  const minutes = MINUTE_VALUES.map((n) => ({
    label: m.timing_minutes({ n: Number(n) }),
    value: n,
  }));
  return (
    <div className="space-y-4">
      <Choice
        id="timing-every"
        items={REPEATS.map((item) => ({
          label: item.label(),
          value: item.value,
        }))}
        label={m.timing_how_often()}
        onChange={(every) => {
          const repeat = REPEATS.find((item) => item.value === every);
          if (repeat) {
            set({ every: repeat.value });
          }
        }}
        value={form.every}
      />
      {form.every === "week" && (
        <FieldSet className="gap-2">
          <FieldLegend variant="label">{m.timing_on_these_days()}</FieldLegend>
          <div className="flex flex-wrap gap-1.5">
            {DAY_NUMBERS.map((day) => (
              <Toggle
                className="h-11 min-w-12"
                key={day}
                onPressedChange={(on) => toggleDay(day, on)}
                pressed={form.days.includes(day)}
                variant="outline"
              >
                {weekdayShort(Number(day))}
              </Toggle>
            ))}
          </div>
        </FieldSet>
      )}
      {form.every === "month" && (
        <Choice
          help={m.timing_days_29_to_31_are_left_out_because()}
          id="timing-month-day"
          items={MONTH_DAYS}
          label={m.timing_day_of_the_month()}
          onChange={(monthDay) => set({ monthDay })}
          value={form.monthDay}
        />
      )}
      {form.every === "hours" && (
        <Choice
          help={m.timing_runs_on_the_hour_counted_from_midnight()}
          id="timing-hours"
          items={hours}
          label={m.timing_every()}
          onChange={(next) => set({ hours: next })}
          value={form.hours}
        />
      )}
      {form.every === "minutes" && (
        <Choice
          help={m.timing_each_run_costs_what_a_normal_run_costs()}
          id="timing-minutes"
          items={minutes}
          label={m.timing_every()}
          onChange={(next) => set({ minutes: next })}
          value={form.minutes}
        />
      )}
      {isTimed(form.every) && (
        <FormField id="timing-time" label={m.timing_at()}>
          <Input
            className="max-w-40"
            id="timing-time"
            onChange={(event) => set({ time: event.target.value })}
            type="time"
            value={form.time}
          />
        </FormField>
      )}
    </div>
  );
}

/** A repeating timetable read back in words, with the next few runs in its time zone. */
export function RunPreview({
  cron,
  timeZone,
}: {
  cron: string;
  timeZone: string;
}) {
  const parsed = parseCron(cron);
  if (!(parsed.ok && isTimeZone(timeZone))) {
    return null;
  }
  if (parsed.bothDays) {
    return (
      <p aria-live="polite" className="border-attention border-l-2 py-0.5 pl-4">
        {m.timing_check_this_expression_limits_both_the_day_of()}
      </p>
    );
  }
  const runs = nextCronRuns(cron, timeZone, 3);
  if (runs.length === 0) {
    return null;
  }
  const format = new Intl.DateTimeFormat(getLocale(), {
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    timeZone,
    timeZoneName: "short",
    weekday: "short",
  });
  const shifting = runs.some((run) => clockChangesNear(run, timeZone));
  const description = describeCron(cron) ?? m.timing_a_custom_timetable();
  return (
    <div aria-live="polite" className="border-l-2 py-0.5 pl-4">
      <p className="font-medium">
        {m.timing_time({ description, zone: timeZone })}
      </p>
      <p className="text-muted-foreground mt-1">{m.timing_next_runs()}</p>
      <ol className="text-muted-foreground tabular-nums">
        {runs.map((run) => (
          <li key={run.getTime()}>
            {format.format(run)}
            {clockChangesNear(run, timeZone) && " *"}
          </li>
        ))}
      </ol>
      {shifting && (
        <p className="text-muted-foreground mt-1 text-xs">
          {m.timing_the_clocks_change_around_this_run_a_time()}
        </p>
      )}
      <p className="text-muted-foreground mt-1 text-xs">
        {m.timing_this_preview_was_calculated_in_your_browser_xaviers()}
      </p>
    </div>
  );
}

/**
 * When something runs: once, on an ordinary repeating pattern, or by a raw cron expression.
 * Both repeating forms read the result back in words with the next runs.
 */
export function TimingEditor({
  form,
  set,
  error,
  allowOnce = true,
}: {
  form: TimingForm;
  set: SetTiming;
  error: string | undefined;
  allowOnce?: boolean;
}) {
  const id = useId();
  const kinds = (
    allowOnce ? KINDS : KINDS.filter((kind) => kind.value !== "at")
  ).map((kind) => ({ label: kind.label(), value: kind.value }));
  // Moving to Advanced starts from whatever was chosen visually.
  const changeKind = (next: unknown) => {
    const kind = kinds.find((item) => item.value === next)?.value;
    if (kind) {
      set(
        kind === "cron" && form.kind === "repeat"
          ? { cron: repeatToCron(form), kind }
          : { kind },
      );
    }
  };

  return (
    <FieldSet className="gap-4">
      <FieldLegend variant="label">{m.timing_when()}</FieldLegend>
      <RadioGroup
        aria-label={m.timing_when()}
        className="flex flex-wrap gap-x-5 gap-y-2"
        onValueChange={changeKind}
        value={form.kind}
      >
        {kinds.map((kind) => (
          <label
            className="flex min-h-11 cursor-pointer items-center gap-2"
            htmlFor={`${id}-${kind.value}`}
            key={kind.value}
          >
            <RadioGroupItem id={`${id}-${kind.value}`} value={kind.value} />
            {kind.label}
          </label>
        ))}
      </RadioGroup>
      {form.kind === "at" && (
        <FormField
          help={m.timing_in_this_devices_local_time()}
          id="timing-at"
          label={m.timing_date_and_time()}
        >
          <Input
            className="max-w-64"
            id="timing-at"
            onChange={(event) => set({ at: event.target.value })}
            type="datetime-local"
            value={form.at}
          />
        </FormField>
      )}
      {form.kind === "repeat" && <RepeatFields form={form} set={set} />}
      {form.kind === "cron" && (
        <FormField
          help={m.timing_five_parts_numbers_only_minute_hour_day_of()}
          id="timing-cron"
          label={m.timing_cron_expression()}
        >
          <Input
            aria-invalid={Boolean(error)}
            autoCapitalize="none"
            className="font-mono"
            id="timing-cron"
            onChange={(event) => set({ cron: event.target.value })}
            spellCheck={false}
            value={form.cron}
          />
        </FormField>
      )}
      {form.kind !== "at" && (
        <FormField
          help={m.timing_times_above_are_in_this_zone_and_follow()}
          id="timing-zone"
          label={m.timing_time_zone()}
        >
          <Input
            autoCapitalize="none"
            className="max-w-72"
            id="timing-zone"
            list={`${id}-zones`}
            onChange={(event) => set({ timeZone: event.target.value })}
            spellCheck={false}
            value={form.timeZone}
          />
          <datalist id={`${id}-zones`}>
            {ZONES.map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </FormField>
      )}
      {error ? (
        <p className="border-destructive border-l-2 py-0.5 pl-4" role="alert">
          {error}
        </p>
      ) : (
        form.kind !== "at" && (
          <RunPreview cron={formCron(form)} timeZone={form.timeZone} />
        )
      )}
    </FieldSet>
  );
}
