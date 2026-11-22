import { describe, expect, it } from "vitest";
import {
  buildTiming,
  describeInterval,
  emptyTiming,
  repeatToCron,
  type TimingForm,
} from "@/features/schedules/timing.ts";

const form = (changes: Partial<TimingForm>): TimingForm => ({
  ...emptyTiming(),
  timeZone: "UTC",
  ...changes,
});

describe("repeatToCron", () => {
  it.each<[Partial<TimingForm>, string]>([
    [{ every: "day", time: "07:05" }, "5 7 * * *"],
    [{ every: "weekdays", time: "09:30" }, "30 9 * * 1-5"],
    [{ days: ["5", "1"], every: "week", time: "09:00" }, "0 9 * * 1,5"],
    [{ every: "month", monthDay: "15", time: "18:00" }, "0 18 15 * *"],
    [{ every: "hours", hours: "1" }, "0 * * * *"],
    [{ every: "hours", hours: "6" }, "0 */6 * * *"],
    [{ every: "minutes", minutes: "15" }, "*/15 * * * *"],
  ])("turns %o into %s", (changes, cron) => {
    expect(repeatToCron(form(changes))).toBe(cron);
  });
});

describe("buildTiming", () => {
  const now = new Date("2026-06-01T12:00:00Z");

  it("sends a one-off time in UTC", () => {
    const at = "2026-06-02T09:00";

    expect(buildTiming(form({ at, kind: "at" }), now)).toEqual({
      timing: { at: new Date(at).toISOString(), time_zone: "UTC" },
    });
  });

  it("refuses a one-off time in the past", () => {
    expect(
      buildTiming(form({ at: "2020-01-01T09:00", kind: "at" }), now).error,
    ).toBe("That time has already passed. Choose one in the future.");
  });

  it("refuses an unknown time zone", () => {
    expect(
      buildTiming(form({ kind: "repeat", timeZone: "Mars/Olympus" }), now)
        .error,
    ).toBe(
      "That time zone is not known. Pick one from the list, such as Europe/Moscow.",
    );
  });

  it("refuses a weekly repeat without days", () => {
    expect(
      buildTiming(form({ days: [], every: "week", kind: "repeat" }), now).error,
    ).toBe("Choose at least one day of the week.");
  });

  it("normalizes the spacing of a cron expression", () => {
    expect(
      buildTiming(form({ cron: " 0  9 * * 1-5 ", kind: "cron" }), now),
    ).toEqual({ timing: { cron: "0 9 * * 1-5", time_zone: "UTC" } });
  });

  it("refuses an expression that never matches a date", () => {
    expect(
      buildTiming(form({ cron: "0 9 31 2 *", kind: "cron" }), now).error,
    ).toBe("This expression never matches a real date, so it would never run.");
  });
});

describe("describeInterval", () => {
  it.each([
    ["PT15M", "15 min"],
    ["PT1H", "1 h"],
    ["P1DT2H", "1 d 2 h"],
    ["PT30S", "30 s"],
    ["soon", "soon"],
  ])("reads %s as %s", (value, text) => {
    expect(describeInterval(value)).toBe(text);
  });
});
