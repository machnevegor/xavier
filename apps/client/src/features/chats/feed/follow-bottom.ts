import { useLayoutEffect, useState } from "react";

/** How close to the end still counts as "at the latest message". */
const NEAR_PX = 96;
const SCROLL_KEYS = new Set([
  " ",
  "ArrowDown",
  "ArrowUp",
  "End",
  "Home",
  "PageDown",
  "PageUp",
]);

function distanceToEnd(): number {
  const root = document.documentElement;
  return root.scrollHeight - window.innerHeight - window.scrollY;
}

function toEnd(behavior: ScrollBehavior): void {
  window.scrollTo({ behavior, top: document.documentElement.scrollHeight });
}

export interface BottomFollower {
  /** Watch an element whose growth should keep the page at its end; a ref callback. */
  observe: (element: HTMLElement | null) => (() => void) | undefined;
  /** Go to the latest message and follow it again. */
  pin: (behavior: ScrollBehavior) => void;
  /** Stop following, before scrolling somewhere else on purpose. */
  release: () => void;
  subscribe: (listener: () => void) => () => void;
  /** Whether the reader has left the latest message. */
  isAway: () => boolean;
}

/**
 * Keeps a window-scrolled conversation at its latest message while the reader is there, and
 * leaves it alone once they scroll up. Only the reader's own input (wheel, touch, keys, the
 * scrollbar) lets go; scrolls made by the page itself, such as the router's, are undone while
 * following. Growth of the observed element — new entries, streamed text, images that load —
 * is followed before it is painted.
 */
function createFollower(): BottomFollower {
  let pinned = true;
  let seeking = false;
  let intent = false;
  let away = false;
  const listeners = new Set<() => void>();

  const setAway = (next: boolean) => {
    if (next !== away) {
      away = next;
      for (const listener of listeners) {
        listener();
      }
    }
  };
  const follow = () => {
    if (pinned) {
      toEnd(seeking ? "smooth" : "instant");
    }
  };
  const onScroll = () => {
    if (distanceToEnd() <= NEAR_PX) {
      pinned = true;
      seeking = false;
    } else if (intent) {
      pinned = false;
      seeking = false;
    } else if (pinned && !seeking) {
      toEnd("instant");
    }
    setAway(!pinned);
  };
  const onIntent = () => {
    intent = true;
  };
  const onKey = (event: KeyboardEvent) => {
    if (SCROLL_KEYS.has(event.key)) {
      intent = true;
    }
  };
  const onPointer = (event: PointerEvent) => {
    if (event.target === document.documentElement) {
      intent = true;
    }
  };

  return {
    isAway: () => away,
    observe: (element) => {
      if (!element) {
        return undefined;
      }
      const shown = () => element.isConnected && !element.closest("[hidden]");
      const followShown = () => {
        if (shown()) {
          follow();
        }
      };
      const onScrollShown = () => {
        if (shown()) {
          onScroll();
        }
      };
      const observer = new ResizeObserver(followShown);
      observer.observe(element);
      window.addEventListener("scroll", onScrollShown, { passive: true });
      window.addEventListener("resize", followShown, { passive: true });
      window.addEventListener("wheel", onIntent, { passive: true });
      window.addEventListener("touchmove", onIntent, { passive: true });
      window.addEventListener("keydown", onKey);
      window.addEventListener("pointerdown", onPointer);
      return () => {
        observer.disconnect();
        window.removeEventListener("scroll", onScrollShown);
        window.removeEventListener("resize", followShown);
        window.removeEventListener("wheel", onIntent);
        window.removeEventListener("touchmove", onIntent);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("pointerdown", onPointer);
      };
    },
    pin: (behavior) => {
      pinned = true;
      intent = false;
      seeking = behavior === "smooth";
      setAway(false);
      toEnd(behavior);
    },
    release: () => {
      pinned = false;
      seeking = false;
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** One follower per conversation page; opening another chat starts again at its end. */
export function useBottomFollower(chatId: string): BottomFollower {
  const [follower] = useState(createFollower);
  useLayoutEffect(() => {
    follower.pin("instant");
  }, [follower, chatId]);
  return follower;
}
