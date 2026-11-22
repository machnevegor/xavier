import { IconChevronDown } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import type { BottomFollower } from "@/features/chats/feed/follow-bottom.ts";
import { isNotable, newSince } from "@/features/chats/feed/notable.ts";
import { m } from "@/paraglide/messages.js";

/** A small spring: up, back, a smaller echo. */
const HOP: Keyframe[] = [
  { transform: "translateY(0)" },
  { offset: 0.25, transform: "translateY(-7px)" },
  { offset: 0.5, transform: "translateY(0)" },
  { offset: 0.7, transform: "translateY(-2.5px)" },
  { transform: "translateY(0)" },
];
/** Without motion, the same moment is a ring that fades. */
const GLOW: Keyframe[] = [
  { boxShadow: "0 0 0 0 var(--ring)" },
  { boxShadow: "0 0 0 4px var(--ring)", offset: 0.3 },
  { boxShadow: "0 0 0 0 transparent" },
];

function nudge(element: HTMLElement | null): void {
  if (!element) {
    return;
  }
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.animate(still ? GLOW : HOP, {
    duration: still ? 900 : 650,
    easing: "cubic-bezier(0.3, 0.7, 0.4, 1)",
  });
}

/**
 * Shown once the reader leaves the latest message, like a messenger's. It nudges when the
 * agent asks for something or finishes, judged from events as they arrive.
 */
export function ScrollToLatest({
  chatId,
  follower,
}: {
  chatId: string;
  follower: BottomFollower;
}) {
  const away = useSyncExternalStore(
    follower.subscribe,
    follower.isAway,
    () => false,
  );
  const button = useRef<HTMLButtonElement>(null);
  const events = useQuery(chatQueries.events(chatId)).data?.items;
  const seen = useRef<string | null>(undefined);

  useEffect(() => {
    if (!events) {
      return;
    }
    const fresh = newSince(events, seen.current);
    seen.current = events.at(-1)?.id ?? null;
    if (follower.isAway() && fresh.some(isNotable)) {
      nudge(button.current);
    }
  }, [events, follower]);

  return (
    <Button
      aria-label={m.chat_scroll_to_the_latest_message()}
      className={cn(
        "bg-background text-muted-foreground hover:text-foreground dark:bg-background size-9 rounded-full shadow-md transition-[opacity,translate] duration-200",
        !away && "pointer-events-none translate-y-2 opacity-0",
      )}
      inert={!away}
      onClick={() => follower.pin("smooth")}
      ref={button}
      size="icon"
      title={m.chat_scroll_to_the_latest_message()}
      variant="outline"
    >
      <IconChevronDown />
    </Button>
  );
}
