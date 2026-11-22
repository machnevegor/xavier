import { isProfileTab, type ProfileTab } from "@/features/chats/model.ts";

export type ChatColumnView = "profile" | "settings";

export function columnSearch(search: Record<string, unknown>): {
  view?: ChatColumnView;
  tab?: ProfileTab;
} {
  const view =
    search.view === "profile" || search.view === "settings"
      ? search.view
      : undefined;
  return {
    tab:
      view === "profile" && isProfileTab(search.tab) ? search.tab : undefined,
    view,
  };
}

let savedScroll: number | null = null;

/** Remember the window scroll before the conversation is hidden. */
export function rememberScroll(): void {
  savedScroll = window.scrollY;
}

/** Take the scroll saved for the conversation, once. */
export function consumeScroll(): number | null {
  const value = savedScroll;
  savedScroll = null;
  return value;
}
