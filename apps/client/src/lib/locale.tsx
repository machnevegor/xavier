import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { m } from "@/paraglide/messages.js";
import { getLocale, setLocale } from "@/paraglide/runtime.js";

/** Autonyms, not translations: each language is named in itself. */
export const LOCALES = [
  { label: "English", value: "en" },
  { label: "Русский", value: "ru" },
] as const;

export type AppLocale = (typeof LOCALES)[number]["value"];

export function isAppLocale(value: string): value is AppLocale {
  return LOCALES.some((locale) => locale.value === value);
}

/**
 * Language selector. Without `onLocale`, a change calls `setLocale` and reloads
 * the same URL. Account passes `onLocale` so it can save the profile first.
 */
export function LocaleSelect({
  disabled = false,
  id,
  onLocale,
}: {
  disabled?: boolean;
  id?: string;
  onLocale?: (locale: AppLocale) => void;
}) {
  const current = getLocale();

  return (
    <Select
      disabled={disabled}
      items={LOCALES.map((locale) => ({
        label: locale.label,
        value: locale.value,
      }))}
      onValueChange={(value) => {
        if (!value || !isAppLocale(value) || value === current) {
          return;
        }
        if (onLocale) {
          onLocale(value);
          return;
        }
        setLocale(value);
      }}
      value={isAppLocale(current) ? current : "en"}
    >
      <SelectTrigger
        aria-label={m.sign_in_language()}
        className="w-full"
        id={id}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {LOCALES.map((locale) => (
          <SelectItem key={locale.value} value={locale.value}>
            {locale.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
