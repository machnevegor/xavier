import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  applyTheme,
  INTENT_COOKIE,
  resolveTheme,
  type ThemeIntent,
  writeCookie,
} from "@/lib/theme.ts";

interface ThemeContextValue {
  intent: ThemeIntent;
  setIntent: (next: ThemeIntent) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({
  children,
  initialIntent,
}: {
  children: ReactNode;
  initialIntent: ThemeIntent;
}) {
  const [intent, setIntentState] = useState(initialIntent);

  useEffect(() => {
    if (intent !== "system") {
      return;
    }

    const query = window.matchMedia("(prefers-color-scheme: dark)");

    const onChange = (event: MediaQueryListEvent) => {
      applyTheme(event.matches ? "dark" : "light");
    };

    query.addEventListener("change", onChange);

    return () => query.removeEventListener("change", onChange);
  }, [intent]);

  const setIntent = useCallback((next: ThemeIntent) => {
    const systemDark = window.matchMedia(
      "(prefers-color-scheme: dark)",
    ).matches;

    writeCookie(INTENT_COOKIE, next);
    applyTheme(resolveTheme(next, systemDark));
    setIntentState(next);
  }, []);

  const value = useMemo(() => ({ intent, setIntent }), [intent, setIntent]);

  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);

  if (!context) {
    throw new Error("useTheme must be used inside ThemeProvider");
  }

  return context;
}
