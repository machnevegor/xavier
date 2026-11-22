import { m } from "@/paraglide/messages.js";
import { getLocale } from "@/paraglide/runtime.js";

/**
 * Cron for the schedule editors: validation, a plain reading, and a preview of upcoming runs
 * in a time zone.
 *
 * Temporal's scheduler is the authority. This module handles only what every cron dialect
 * reads the same way: five numeric fields (minute hour day-of-month month weekday) with `*`,
 * lists, ranges and steps. Names, seconds, years and shortcuts are rejected here, and an
 * expression that restricts both day fields is accepted but not previewed, because
 * schedulers disagree on whether a day must match both or either.
 */

interface FieldRange {
  label: () => string;
  min: number;
  max: number;
  weekday?: boolean;
}

const FIELDS: readonly FieldRange[] = [
  { label: m.timing_minute, max: 59, min: 0 },
  { label: m.timing_hour_2, max: 23, min: 0 },
  { label: m.timing_day_of_the_month_2, max: 31, min: 1 },
  { label: m.timing_month, max: 12, min: 1 },
  { label: m.timing_weekday, max: 7, min: 0, weekday: true },
];
const MINUTE = 60_000;
const DAY = 86_400_000;
const SUNDAY = Date.UTC(2023, 0, 1);

/** Short weekday name for a cron day, 0 is Sunday. Shown to people, so it follows the locale. */
export function weekdayShort(index: number): string {
  return new Intl.DateTimeFormat(getLocale(), {
    timeZone: "UTC",
    weekday: "short",
  }).format(SUNDAY + index * DAY);
}

export interface CronField {
  any: boolean;
  values: Set<number>;
}

export type ParsedCron =
  | {
      ok: true;
      parts: string[];
      minute: CronField;
      hour: CronField;
      day: CronField;
      month: CronField;
      weekday: CronField;
      bothDays: boolean;
    }
  | { ok: false; error: string };

class CronError extends Error {}

function parseField(text: string, field: FieldRange): CronField {
  const { label, min, max } = field;
  const values = new Set<number>();
  for (const part of text.split(",")) {
    const match = /^(\*|(\d+)(?:-(\d+))?)(?:\/(\d+))?$/.exec(part);
    if (!match) {
      throw new CronError(
        m.timing_the_part_is_not_valid_use_a_number({
          field: label(),
          text,
        }),
      );
    }
    const [, head, first, last, stepText] = match;
    const step = stepText === undefined ? 1 : Number(stepText);
    const from = head === "*" ? min : Number(first);
    const to =
      head === "*"
        ? max
        : last !== undefined
          ? Number(last)
          : stepText !== undefined
            ? max
            : from;
    if (from < min || to > max) {
      throw new CronError(
        m.timing_the_part_must_stay_between_and_does_not({
          field: label(),
          max,
          min,
          part,
        }),
      );
    }
    if (from > to) {
      throw new CronError(
        m.timing_in_the_part_the_range_runs_backwards({
          field: label(),
          part,
        }),
      );
    }
    if (step < 1) {
      throw new CronError(
        m.timing_in_the_part_a_step_must_be_1({ field: label() }),
      );
    }
    for (let value = from; value <= to; value += step) {
      values.add(field.weekday ? value % 7 : value);
    }
  }
  return { any: text === "*", values };
}

export function parseCron(expression: string): ParsedCron {
  const text = expression.trim();
  const parts = text.split(/\s+/).filter(Boolean);
  if (/[a-z@=]/i.test(text)) {
    return {
      error: m.timing_this_editor_reads_numbers_only_names_such_as(),
      ok: false,
    };
  }
  if (parts.length !== FIELDS.length) {
    return {
      error: m.timing_use_five_parts_minute_hour_day_of_the({
        n: parts.length,
      }),
      ok: false,
    };
  }
  try {
    const [minute, hour, day, month, weekday] = FIELDS.map((field, index) =>
      parseField(parts[index] ?? "", field),
    ) as [CronField, CronField, CronField, CronField, CronField];
    return {
      bothDays: !day.any && !weekday.any,
      day,
      hour,
      minute,
      month,
      ok: true,
      parts,
      weekday,
    };
  } catch (error) {
    if (error instanceof CronError) {
      return { error: error.message, ok: false };
    }
    throw error;
  }
}

export function isTimeZone(timeZone: string): boolean {
  if (!timeZone) {
    return false;
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone });
    return true;
  } catch {
    return false;
  }
}

const two = (value: string) => value.padStart(2, "0");
const isNumber = (text: string) => /^\d+$/.test(text);
const stepOf = (text: string) => /^\*\/(\d+)$/.exec(text)?.[1];

/** The common shapes in words; anything else is honestly called custom. */
export function describeCron(expression: string): string | null {
  const cron = parseCron(expression);
  if (!cron.ok) {
    return null;
  }
  const [minute = "", hour = "", day = "", month = "", weekday = ""] =
    cron.parts;
  const custom = m.timing_a_custom_timetable();
  if (month !== "*") {
    return custom;
  }
  const everyDay = day === "*" && weekday === "*";
  const minuteStep = stepOf(minute);
  const hourStep = stepOf(hour);
  if (everyDay && hour === "*") {
    if (minute === "*" || minuteStep === "1") {
      return m.timing_every_minute();
    }
    if (minuteStep) {
      return m.timing_every_minutes({ count: Number(minuteStep) });
    }
    if (isNumber(minute)) {
      return m.timing_every_hour_at_minutes_past({ mm: two(minute) });
    }
  }
  if (everyDay && hourStep && isNumber(minute)) {
    return m.timing_every_hours_at_minutes_past({
      count: Number(hourStep),
      mm: two(minute),
    });
  }
  if (!(isNumber(minute) && isNumber(hour))) {
    return custom;
  }
  const at = m.timing_at_2({ time: `${two(hour)}:${two(minute)}` });
  if (everyDay) {
    return m.timing_every_day_2({ at });
  }
  if (day === "*") {
    if (weekday === "1-5") {
      return m.timing_every_weekday({ at });
    }
    const days = [...cron.weekday.values]
      .sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7))
      .map((value) => weekdayShort(value))
      .join(", ");
    return m.timing_every_3({ at, days });
  }
  if (weekday === "*" && isNumber(day)) {
    return m.timing_on_day_of_every_month({ at, day });
  }
  return custom;
}

interface WallTime {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

const zoneFormatter = (timeZone: string) =>
  new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    hour: "numeric",
    hourCycle: "h23",
    minute: "numeric",
    month: "numeric",
    timeZone,
    year: "numeric",
  });

function wallTime(formatter: Intl.DateTimeFormat, instant: number): WallTime {
  const parts = Object.fromEntries(
    formatter
      .formatToParts(instant)
      .map((part) => [part.type, Number(part.value)]),
  );
  return {
    day: parts.day ?? 0,
    hour: parts.hour ?? 0,
    minute: parts.minute ?? 0,
    month: parts.month ?? 0,
    year: parts.year ?? 0,
  };
}

const asUtc = (wall: WallTime) =>
  Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);

/** True when the zone's clocks change within half a day of this moment. */
export function clockChangesNear(date: Date, timeZone: string): boolean {
  const formatter = zoneFormatter(timeZone);
  const offset = (instant: number) =>
    asUtc(wallTime(formatter, instant)) - instant;
  const at = Math.floor(date.getTime() / MINUTE) * MINUTE;
  return offset(at - DAY / 2) !== offset(at + DAY / 2);
}

/**
 * The next `count` times the expression fires, read as wall-clock time in the zone. A time
 * skipped when clocks go forward is left out; one that happens twice is listed once, at its
 * first occurrence. Returns nothing for an expression that restricts both day fields.
 */
export function nextCronRuns(
  expression: string,
  timeZone: string,
  count = 3,
  from: Date = new Date(),
): Date[] {
  const cron = parseCron(expression);
  if (!cron.ok || cron.bothDays || !isTimeZone(timeZone)) {
    return [];
  }
  const formatter = zoneFormatter(timeZone);
  const start = wallTime(formatter, from.getTime());
  const offsetAt = (instant: number) =>
    asUtc(wallTime(formatter, instant)) - instant;
  const hours = [...cron.hour.values].sort((a, b) => a - b);
  const minutes = [...cron.minute.values].sort((a, b) => a - b);
  const runs: number[] = [];
  for (let offset = -1; offset < 1500 && runs.length < count; offset += 1) {
    const date = new Date(
      Date.UTC(start.year, start.month - 1, start.day + offset),
    );
    if (
      !(
        cron.month.values.has(date.getUTCMonth() + 1) &&
        cron.day.values.has(date.getUTCDate()) &&
        cron.weekday.values.has(date.getUTCDay())
      )
    ) {
      continue;
    }
    for (const hour of hours) {
      for (const minute of minutes) {
        const guess = asUtc({
          day: date.getUTCDate(),
          hour,
          minute,
          month: date.getUTCMonth() + 1,
          year: date.getUTCFullYear(),
        });
        // Read the wall time with the zone's offset on either side of that day: neither fits
        // when a clock change skips it, both fit when it repeats and the earlier is taken.
        const fits = [
          guess - offsetAt(guess - DAY),
          guess - offsetAt(guess + DAY),
        ].filter((instant) => asUtc(wallTime(formatter, instant)) === guess);
        if (fits.length === 0) {
          continue;
        }
        const instant = Math.min(...fits);
        if (instant > from.getTime() && runs.length < count) {
          runs.push(instant);
        }
      }
    }
  }
  return runs.sort((a, b) => a - b).map((instant) => new Date(instant));
}
