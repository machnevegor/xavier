import { createIsomorphicFn } from "@tanstack/react-start";
import { getCookie } from "@tanstack/react-start/server";

export type ThemeIntent = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export interface ThemeState {
  intent: ThemeIntent;
  resolved: ResolvedTheme;
}

export const INTENT_COOKIE = "theme-intent";
export const RESOLVED_COOKIE = "theme-resolved";
export const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export function isThemeIntent(value: unknown): value is ThemeIntent {
  return value === "system" || value === "light" || value === "dark";
}

function toIntent(value: string | undefined): ThemeIntent {
  return isThemeIntent(value) ? value : "system";
}

export function resolveTheme(
  intent: ThemeIntent,
  systemDark: boolean,
): ResolvedTheme {
  return intent === "system" ? (systemDark ? "dark" : "light") : intent;
}

function readCookie(name: string): string | undefined {
  return document.cookie
    .split("; ")
    .find((entry) => entry.startsWith(`${name}=`))
    ?.slice(name.length + 1);
}

export function writeCookie(name: string, value: string): void {
  // biome-ignore lint/suspicious/noDocumentCookie: Cookie Store API is async and unsupported in Firefox
  document.cookie = `${name}=${value}; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`;
}

export function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.classList.toggle("dark", resolved === "dark");
  writeCookie(RESOLVED_COOKIE, resolved);
}

export const getThemeState = createIsomorphicFn()
  .server((): ThemeState => {
    const intent = toIntent(getCookie(INTENT_COOKIE));

    return {
      intent,
      resolved: resolveTheme(intent, getCookie(RESOLVED_COOKIE) === "dark"),
    };
  })
  .client((): ThemeState => {
    const intent = toIntent(readCookie(INTENT_COOKIE));

    return {
      intent,
      resolved: resolveTheme(
        intent,
        window.matchMedia("(prefers-color-scheme: dark)").matches,
      ),
    };
  });
