import { useState, useSyncExternalStore } from "react";

const KEY = "xavier.sidebar";
const WIDE = "(min-width: 1024px)";

type Choices = Partial<Record<"wide" | "narrow", boolean>>;

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(WIDE);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function readChoices(): Choices {
  try {
    const parsed: unknown = JSON.parse(
      window.localStorage.getItem(KEY) ?? "{}",
    );
    if (typeof parsed !== "object" || parsed === null) {
      return {};
    }
    const { wide, narrow } = parsed as Record<string, unknown>;
    return {
      narrow: typeof narrow === "boolean" ? narrow : undefined,
      wide: typeof wide === "boolean" ? wide : undefined,
    };
  } catch {
    return {};
  }
}

/**
 * The sidebar is a rail by default where space is short and full where it is not. A choice
 * is remembered separately for each, so collapsing it on a tablet keeps it open on a desktop.
 */
export function useSidebarCollapsed(): [boolean, () => void] {
  const wide = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(WIDE).matches,
  );
  const [choices, setChoices] = useState(readChoices);
  const size = wide ? "wide" : "narrow";
  const collapsed = choices[size] ?? !wide;
  const toggle = () => {
    const next = { ...choices, [size]: !collapsed };
    setChoices(next);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Storage can be disabled; the choice then lasts for this page only.
    }
  };
  return [collapsed, toggle];
}
