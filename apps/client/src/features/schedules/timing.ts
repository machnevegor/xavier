import type { ChatTiming } from "@/api/types.ts";
import {
  describeCron,
  isTimeZone,
  nextCronRuns,
  parseCron,
} from "@/features/schedules/cron.ts";
import { dateTime } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export type TimingKind = "at" | "repeat" | "cron";
export type Repeat =
  | "day"
  | "weekdays"
  | "week"
  | "month"
  | "hours"
  | "minutes";

export const REPEATS: readonly { value: Repeat; label: () => string }[] = [
  { label: m.timing_every_day, value: "day" },
  { label: m.timing_every_weekday_mon_to_fri, value: "weekdays" },
  { label: m.timing_on_chosen_days_of_the_week, value: "week" },
  { label: m.timing_once_a_month, value: "month" },
  { label: m.timing_every_few_hours, value: "hours" },
  { label: m.timing_every_few_minutes, value: "minutes" },
];

export interface TimingForm {
  kind: TimingKind;
  /** `datetime-local` value, in this device's time. */
  at: string;
  every: Repeat;
  /** `HH:MM`. */
  time: string;
  /** Weekday numbers, 0 is Sunday. */
  days: string[];
  monthDay: string;
  hours: string;
  minutes: string;
  cron: string;
  timeZone: string;
}

export const emptyTiming = (): TimingForm => ({
  at: "",
  cron: "0 9 * * 1-5",
  days: ["1"],
  every: "weekdays",
  hours: "1",
  kind: "at",
  minutes: "15",
  monthDay: "1",
  time: "09:00",
  timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
});

export const isTimed = (every: Repeat) =>
  every !== "hours" && every !== "minutes";

/** The visual choices as the cron expression the backend stores. */
export function repeatToCron(form: TimingForm): string {
  if (form.every === "minutes") {
    return `*/${form.minutes} * * * *`;
  }
  if (form.every === "hours") {
    return form.hours === "1" ? "0 * * * *" : `0 */${form.hours} * * *`;
  }
  const [hour = 0, minute = 0] = form.time.split(":").map(Number);
  const at = `${minute} ${hour}`;
  switch (form.every) {
    case "weekdays":
      return `${at} * * 1-5`;
    case "week":
      return `${at} * * ${[...form.days].sort().join(",")}`;
    case "month":
      return `${at} ${form.monthDay} * *`;
    default:
      return `${at} * * *`;
  }
}

export const formCron = (form: TimingForm): string =>
  form.kind === "repeat"
    ? repeatToCron(form)
    : form.cron.trim().replace(/\s+/g, " ");

/** The `timing` the API takes, or the first problem in words. */
export function buildTiming(
  form: TimingForm,
  now: Date = new Date(),
): { timing: ChatTiming; error?: never } | { timing?: never; error: string } {
  if (form.kind === "at") {
    if (!form.at) {
      return { error: m.timing_choose_a_date_and_time() };
    }
    const when = new Date(form.at);
    if (Number.isNaN(when.getTime())) {
      return { error: m.timing_choose_a_date_and_time() };
    }
    if (when <= now) {
      return { error: m.timing_that_time_has_already_passed_choose_one_in() };
    }
    return { timing: { at: when.toISOString(), time_zone: "UTC" } };
  }
  if (!isTimeZone(form.timeZone)) {
    return { error: m.timing_that_time_zone_is_not_known_pick_one() };
  }
  if (form.kind === "repeat") {
    if (isTimed(form.every) && !/^\d\d:\d\d$/.test(form.time)) {
      return { error: m.timing_choose_a_time_of_day() };
    }
    if (form.every === "week" && form.days.length === 0) {
      return { error: m.timing_choose_at_least_one_day_of_the_week() };
    }
    return { timing: { cron: repeatToCron(form), time_zone: form.timeZone } };
  }
  const cron = formCron(form);
  const parsed = parseCron(cron);
  if (!parsed.ok) {
    return { error: parsed.error };
  }
  if (
    !parsed.bothDays &&
    nextCronRuns(cron, form.timeZone, 1, now).length === 0
  ) {
    return { error: m.timing_this_expression_never_matches_a_real_date_so() };
  }
  return { timing: { cron, time_zone: form.timeZone } };
}

/** ISO 8601 durations as the API sends them (PT15M, PT1H, P1D) in plain words. */
export function describeInterval(value: string): string {
  const match =
    /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/.exec(
      value,
    );
  if (!match) {
    return value;
  }
  const [, days, hours, minutes, secs] = match;
  const parts: string[] = [];
  if (days) {
    parts.push(m.timing_d({ n: days }));
  }
  if (hours) {
    parts.push(m.timing_h({ n: hours }));
  }
  if (minutes) {
    parts.push(m.timing_min({ n: minutes }));
  }
  if (secs) {
    parts.push(m.timing_s({ n: secs }));
  }
  return parts.join(" ") || value;
}

export function describeTiming(timing: Partial<ChatTiming>): string {
  if (timing.at) {
    return m.timing_once_2({ dateTime: dateTime(timing.at) });
  }
  if (timing.interval) {
    return m.timing_every_2({ interval: describeInterval(timing.interval) });
  }
  if (timing.cron) {
    return m.timing_time_2({
      cron:
        describeCron(timing.cron) ?? m.timing_cron({ expression: timing.cron }),
      zone: timing.time_zone ?? "UTC",
    });
  }
  return m.timing_no_timing();
}
