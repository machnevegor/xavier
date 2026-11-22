import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bytes,
  count,
  dateTime,
  day,
  seconds,
  timeAgo,
  timeUntil,
  usd,
} from "@/lib/format.ts";
import { overwriteGetLocale } from "@/paraglide/runtime.js";

function locale(value: "en" | "ru") {
  overwriteGetLocale(() => value);
}

afterEach(() => {
  locale("en");
  vi.useRealTimers();
});

describe("usd", () => {
  it("shows cents for round amounts and large ones", () => {
    locale("en");
    expect(usd(5)).toBe("$5.00");
    expect(usd(0.13)).toBe("$0.13");
    expect(usd(12.5)).toBe("$12.50");
    expect(usd(0)).toBe("$0.00");
    expect(usd(null)).toBe("$0.00");
  });

  it("keeps a third digit where a small amount needs it", () => {
    locale("en");
    expect(usd(0.007)).toBe("$0.007");
    expect(usd(0.1452)).toBe("$0.145");
  });

  it("uses Russian grouping and the dollar sign after the amount", () => {
    locale("ru");
    expect(usd(5)).toBe("5,00\u00a0$");
    expect(usd(0.13)).toBe("0,13\u00a0$");
    expect(usd(12.5)).toBe("12,50\u00a0$");
    expect(usd(1.23)).toBe("1,23\u00a0$");
    expect(usd(0.007)).toBe("0,007\u00a0$");
    expect(usd(0.1452)).toBe("0,145\u00a0$");
  });
});

describe("dates", () => {
  it("names distances, days and clock times in English", () => {
    locale("en");
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 10, 22, 14, 30, 0));
    expect(timeAgo(null)).toBe("never");
    expect(timeAgo(new Date(2026, 10, 22, 14, 25, 0).toISOString())).toBe(
      "5 minutes ago",
    );
    expect(timeUntil(undefined)).toBe("not planned");
    expect(timeUntil(new Date(2026, 10, 22, 14, 35, 0).toISOString())).toBe(
      "in 5 minutes",
    );
    expect(dateTime(new Date(2026, 10, 22, 14, 30, 0).toISOString())).toBe(
      "22 Nov 2026, 14:30",
    );
    expect(dateTime(null)).toBe("—");
    expect(day(new Date(2026, 10, 22))).toBe("November 22");
    expect(day(new Date(2025, 10, 22))).toBe("November 22, 2025");
  });

  it("names distances, days and clock times in Russian", () => {
    locale("ru");
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 10, 22, 14, 30, 0));
    expect(timeAgo(null)).toBe("никогда");
    expect(timeAgo(new Date(2026, 10, 22, 14, 25, 0).toISOString())).toBe(
      "5 минут назад",
    );
    expect(timeUntil(undefined)).toBe("не запланировано");
    expect(timeUntil(new Date(2026, 10, 22, 14, 35, 0).toISOString())).toBe(
      "через 5 минут",
    );
    expect(dateTime(new Date(2026, 10, 22, 14, 30, 0).toISOString())).toBe(
      "22 нояб. 2026, 14:30",
    );
    expect(day(new Date(2026, 10, 22))).toBe("22 ноября");
    expect(day(new Date(2025, 10, 22))).toBe("22 ноября 2025 г.");
  });
});

describe("quantities", () => {
  it("groups counts and names units in English", () => {
    locale("en");
    expect(count(1234)).toBe("1,234");
    expect(count(null)).toBe("0");
    expect(bytes(0)).toBe("0 B");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(seconds(5)).toBe("5s");
    expect(seconds(90)).toBe("1m 30s");
    expect(seconds(3661)).toBe("1h 1m");
  });

  it("groups counts and names units in Russian", () => {
    locale("ru");
    expect(count(1234)).toBe("1\u00a0234");
    expect(bytes(0)).toBe("0 Б");
    expect(bytes(1536)).toBe("1.5 КБ");
    expect(seconds(5)).toBe("5 с");
    expect(seconds(90)).toBe("1 мин 30 с");
    expect(seconds(3661)).toBe("1 ч 1 мин");
  });
});
