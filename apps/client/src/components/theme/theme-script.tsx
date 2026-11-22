import { ScriptOnce } from "@tanstack/react-router";
import { COOKIE_MAX_AGE, INTENT_COOKIE, RESOLVED_COOKIE } from "@/lib/theme.ts";

const script = `const intent = document.cookie.match(/(?:^|; )${INTENT_COOKIE}=([^;]*)/)?.[1] ?? "system";
const dark = intent === "dark" || (intent !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
document.documentElement.classList.toggle("dark", dark);
document.cookie = "${RESOLVED_COOKIE}=" + (dark ? "dark" : "light") + "; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax";`;

export function ThemeScript() {
  return <ScriptOnce>{script}</ScriptOnce>;
}
