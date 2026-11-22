import { format, formatDistanceToNowStrict } from "date-fns";
import { enUS, ru } from "date-fns/locale";
import { m } from "@/paraglide/messages.js";
import { getLocale } from "@/paraglide/runtime.js";

type Moment = string | null | undefined;

const DATE_LOCALES = { en: enUS, ru } as const;

const dateLocale = () => DATE_LOCALES[getLocale()];

const distance = (iso: string): string =>
  formatDistanceToNowStrict(new Date(iso), {
    addSuffix: true,
    locale: dateLocale(),
  });

export const timeAgo = (iso: Moment): string =>
  iso ? distance(iso) : m.format_never();

export const timeUntil = (iso: Moment): string =>
  iso ? distance(iso) : m.format_not_planned();

export const dateTime = (iso: Moment): string =>
  iso
    ? format(new Date(iso), "d MMM yyyy, HH:mm", { locale: dateLocale() })
    : m.common_text();

export const clock = (iso: Moment): string =>
  iso
    ? format(new Date(iso), "HH:mm:ss", { locale: dateLocale() })
    : m.common_text();

export const hourMinute = (iso: Moment): string =>
  iso
    ? format(new Date(iso), "HH:mm", { locale: dateLocale() })
    : m.common_text();

/** A calendar day as a messenger names it: the year only when it is not this one. */
export const day = (date: Date): string => {
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return new Intl.DateTimeFormat(getLocale(), {
    day: "numeric",
    month: "long",
    ...(sameYear ? {} : { year: "numeric" as const }),
  }).format(date);
};

/** Cents, and a third digit only where a small amount needs it: $5.00, $0.13, $0.007. */
export const usd = (value: number | null | undefined): string => {
  const amount = value ?? 0;
  const fine = amount.toFixed(3);
  const digits = amount >= 10 || fine.endsWith("0") ? 2 : 3;
  return new Intl.NumberFormat(getLocale(), {
    currency: "USD",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
    style: "currency",
  }).format(amount);
};

export const count = (value: number | null | undefined): string =>
  new Intl.NumberFormat(getLocale()).format(value ?? 0);

const BYTE_UNITS = [m.format_b, m.format_kb, m.format_mb, m.format_gb] as const;

export function bytes(value: number | null | undefined): string {
  let size = value ?? 0;
  let unit = 0;
  while (size >= 1024 && unit < BYTE_UNITS.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const rounded = size.toFixed(unit === 0 || size >= 10 ? 0 : 1);
  return `${rounded} ${BYTE_UNITS[unit]()}`;
}

export function seconds(value: number | null | undefined): string {
  const total = Math.round(value ?? 0);
  if (total < 60) {
    return m.format_s({ s: total });
  }
  if (total < 3600) {
    return m.format_m_s({ m: Math.floor(total / 60), s: total % 60 });
  }
  return m.format_h_m({
    h: Math.floor(total / 3600),
    m: Math.floor((total % 3600) / 60),
  });
}

export const between = (start: Moment, end: Moment): string =>
  start && end
    ? seconds((new Date(end).getTime() - new Date(start).getTime()) / 1000)
    : m.common_text();

export const shortId = (id: Moment): string => (id ? id.slice(0, 8) : "");
