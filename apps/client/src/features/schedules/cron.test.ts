import { describe, expect, it } from "vitest";
import {
  describeCron,
  nextCronRuns,
  parseCron,
} from "@/features/schedules/cron.ts";

const iso = (dates: Date[]) => dates.map((date) => date.toISOString());

describe("parseCron", () => {
  it("expands lists, ranges and steps", () => {
    const cron = parseCron("*/20 9-11 * * 1,3");

    expect(cron.ok && [...cron.minute.values]).toEqual([0, 20, 40]);
    expect(cron.ok && [...cron.hour.values]).toEqual([9, 10, 11]);
    expect(cron.ok && [...cron.weekday.values]).toEqual([1, 3]);
  });

  it("reads weekday 7 as Sunday", () => {
    const cron = parseCron("0 0 * * 7");

    expect(cron.ok && [...cron.weekday.values]).toEqual([0]);
  });

  it.each([
    [
      "0 9 * * MON",
      "This editor reads numbers only. Names such as MON or JAN and shortcuts such as @daily are not supported here; write 1 for Monday, and so on.",
    ],
    [
      "@daily",
      "This editor reads numbers only. Names such as MON or JAN and shortcuts such as @daily are not supported here; write 1 for Monday, and so on.",
    ],
    [
      "0 9 * *",
      "Use five parts: minute, hour, day of the month, month and weekday. This one has 4. Seconds and years are not supported here.",
    ],
    [
      "60 * * * *",
      'The minute part must stay between 0 and 59; "60" does not.',
    ],
    ["5-1 * * * *", 'In the minute part, the range "5-1" runs backwards.'],
    ["*/0 * * * *", "In the minute part, a step must be 1 or more."],
  ])("rejects %s", (expression, reason) => {
    const cron = parseCron(expression);

    expect(cron.ok).toBe(false);
    expect(!cron.ok && cron.error).toBe(reason);
  });
});

describe("describeCron", () => {
  it.each([
    ["* * * * *", "Every minute"],
    ["*/15 * * * *", "Every 15 minutes"],
    ["5 * * * *", "Every hour, at 05 minutes past"],
    ["0 */6 * * *", "Every 6 hours, at 00 minutes past"],
    ["30 8 * * *", "Every day at 08:30"],
    ["0 9 * * 1-5", "Every weekday at 09:00"],
    ["30 8 * * 0,6", "Every Sat, Sun at 08:30"],
    ["0 0 1 * *", "On day 1 of every month at 00:00"],
    ["0 9 * 1 *", "A custom timetable"],
  ])("reads %s as %s", (expression, text) => {
    expect(describeCron(expression)).toBe(text);
  });

  it("does not describe an invalid expression", () => {
    expect(describeCron("0 9 * *")).toBeNull();
  });
});

describe("nextCronRuns", () => {
  it("lists the next runs after the given moment", () => {
    const runs = nextCronRuns(
      "0 9 * * *",
      "UTC",
      2,
      new Date("2026-03-01T10:00:00Z"),
    );

    expect(iso(runs)).toEqual([
      "2026-03-02T09:00:00.000Z",
      "2026-03-03T09:00:00.000Z",
    ]);
  });

  it("skips a time that clocks jump over", () => {
    const runs = nextCronRuns(
      "30 2 * * *",
      "Europe/Berlin",
      2,
      new Date("2026-03-28T00:00:00Z"),
    );

    expect(iso(runs)).toEqual([
      "2026-03-28T01:30:00.000Z",
      "2026-03-30T00:30:00.000Z",
    ]);
  });

  it("lists a repeated time once, at its first occurrence", () => {
    const runs = nextCronRuns(
      "30 2 * * *",
      "Europe/Berlin",
      2,
      new Date("2026-10-24T12:00:00Z"),
    );

    expect(iso(runs)).toEqual([
      "2026-10-25T00:30:00.000Z",
      "2026-10-26T01:30:00.000Z",
    ]);
  });

  it("does not preview an expression that restricts both day fields", () => {
    expect(nextCronRuns("0 9 1 * 1", "UTC")).toEqual([]);
  });
});
