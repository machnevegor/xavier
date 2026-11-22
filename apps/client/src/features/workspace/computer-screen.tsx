import type RFB from "@novnc/novnc";
import {
  IconDeviceDesktop,
  IconKeyboard,
  IconMaximize,
  IconMinimize,
  IconX,
} from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { cn } from "cn";
import { type RefObject, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/errors.ts";
import type { Control } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { setControl } from "@/features/workspace/api.ts";
import { clock } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** Close codes of the screen stream (src/api/routers/computer.py). */
const CLOSE_UNAUTHORIZED = 4401;
const CLOSE_NOT_FOUND = 4404;
const CLOSE_CONFLICT = 4409;
const CONTROL_NOT_HELD = "control_not_held";
const WAKE_RETRY_MS = 3000;
const MAX_DELAY_MS = 15_000;

type Screen =
  | { state: "connecting" | "live" | "waking" | "reconnecting" }
  | { state: "failed"; error: ApiError };

const SCREEN_NOTE: Record<
  Exclude<Screen["state"], "failed" | "live">,
  () => string
> = {
  connecting: m.chat_connecting_to_the_screen,
  reconnecting: m.chat_connection_lost_reconnecting,
  waking: m.chat_waking_the_workspace,
};

/**
 * The live screen over the API's RFB relay. The socket is opened here, not by noVNC, because
 * the relay explains itself in close codes: hibernation (retry once awake), lost control
 * (fall back to watching), and errors that end the stream.
 */
function useScreen(
  container: RefObject<HTMLDivElement | null>,
  rfb: RefObject<RFB | null>,
  chatId: string,
  control: boolean,
  onControlLost: () => void,
): Screen {
  const [screen, setScreen] = useState<Screen>({ state: "connecting" });
  const lost = useRef(onControlLost);
  lost.current = onControlLost;

  useEffect(() => {
    const target = container.current;
    if (!target) {
      return;
    }
    let disposed = false;
    let client: RFB | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    const retry = (state: "waking" | "reconnecting", delay: number) => {
      setScreen({ state });
      timer = setTimeout(() => void connect(), delay);
    };

    async function connect(): Promise<void> {
      const { default: Client } = await import("@novnc/novnc");
      if (disposed || !target) {
        return;
      }
      const url = new URL(
        `/api/v1/chats/${encodeURIComponent(chatId)}/computer/vnc`,
        window.location.href,
      );
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      if (control) {
        url.searchParams.set("control", "true");
      }
      const socket = new WebSocket(url, ["binary"]);
      socket.addEventListener("close", ({ code, reason }) => {
        if (disposed) {
          return;
        }
        client = null;
        rfb.current = null;
        if (code === CLOSE_UNAUTHORIZED) {
          setScreen({
            error: new ApiError(401, "Not signed in"),
            state: "failed",
          });
        } else if (code === CLOSE_NOT_FOUND) {
          setScreen({
            error: new ApiError(404, "Not found"),
            state: "failed",
          });
        } else if (code === CLOSE_CONFLICT && reason === CONTROL_NOT_HELD) {
          lost.current();
        } else if (code === CLOSE_CONFLICT) {
          retry("waking", WAKE_RETRY_MS);
        } else {
          attempt += 1;
          retry(
            "reconnecting",
            Math.min(1000 * 2 ** (attempt - 1), MAX_DELAY_MS),
          );
        }
      });
      client = new Client(target, socket);
      rfb.current = client;
      client.viewOnly = !control;
      client.focusOnClick = control;
      client.scaleViewport = true;
      client.addEventListener("connect", () => {
        attempt = 0;
        setScreen({ state: "live" });
      });
    }

    setScreen({ state: "connecting" });
    void connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      client?.disconnect();
      rfb.current = null;
    };
  }, [container, rfb, chatId, control]);

  return screen;
}

/** X11 keysyms of the keys a phone keyboard sends without producing text. */
const KEYSYMS: Record<string, number> = {
  ArrowDown: 0xff54,
  ArrowLeft: 0xff51,
  ArrowRight: 0xff53,
  ArrowUp: 0xff52,
  Delete: 0xffff,
  End: 0xff57,
  Escape: 0xff1b,
  Home: 0xff50,
  Tab: 0xff09,
};
const BACKSPACE = 0xff08;
const RETURN = 0xff0d;

/** Latin-1 keysyms equal the code point; every other character has a Unicode keysym. */
function keysymOf(char: string): number {
  if (char === "\n") {
    return RETURN;
  }
  const code = char.codePointAt(0) ?? 0;
  return code < 0x100 ? code : 0x0100_0000 + code;
}

/*
 * Phone keyboards report most keys as "Unidentified" and compose words, so typing is read from
 * what the field's text becomes: it always holds this filler, removed characters are sent as
 * Backspace and added ones as themselves.
 */
const FILLER = "_".repeat(32);

/**
 * A hidden field that raises the phone's keyboard and types into the Computer. Keys reach the
 * screen only while the connection was opened with control; a watching connection ignores them.
 */
function RemoteKeyboard({
  rfb,
  field,
}: {
  rfb: RefObject<RFB | null>;
  field: RefObject<HTMLTextAreaElement | null>;
}) {
  const last = useRef(FILLER);
  const send = (keysym: number) => {
    const client = rfb.current;
    if (client && !client.viewOnly) {
      client.sendKey(keysym, null);
    }
  };
  const reset = (target: HTMLTextAreaElement) => {
    target.value = FILLER;
    target.setSelectionRange(FILLER.length, FILLER.length);
    last.current = FILLER;
  };

  return (
    <textarea
      aria-label={m.chat_type_on_the_computer()}
      autoCapitalize="off"
      autoComplete="off"
      autoCorrect="off"
      className="pointer-events-none absolute bottom-0 left-0 size-px resize-none overflow-hidden text-base opacity-0"
      defaultValue={FILLER}
      onFocus={(event) => reset(event.currentTarget)}
      onInput={(event) => {
        const target = event.currentTarget;
        const next = target.value;
        const previous = last.current;
        let same = 0;
        while (
          same < previous.length &&
          same < next.length &&
          previous[same] === next[same]
        ) {
          same += 1;
        }
        for (let index = same; index < previous.length; index += 1) {
          send(BACKSPACE);
        }
        for (const char of next.slice(same)) {
          send(keysymOf(char));
        }
        last.current = next;
        if (!(event.nativeEvent as InputEvent).isComposing) {
          reset(target);
        }
      }}
      onKeyDown={(event) => {
        const keysym = KEYSYMS[event.key];
        if (keysym !== undefined && !event.nativeEvent.isComposing) {
          event.preventDefault();
          send(keysym);
        }
      }}
      ref={field}
      spellCheck={false}
      tabIndex={-1}
    />
  );
}

/**
 * Fullscreen where the browser allows an element to have it, otherwise (an iPhone) the frame
 * covers the viewport. Either way the screen scales to fit and keeps its proportions.
 */
function useFullscreen(frame: RefObject<HTMLDivElement | null>) {
  const [native, setNative] = useState(false);
  const [covering, setCovering] = useState(false);

  useEffect(() => {
    const sync = () =>
      setNative(
        frame.current !== null && document.fullscreenElement === frame.current,
      );
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [frame]);

  useEffect(() => {
    if (!covering) {
      return;
    }
    const exit = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !(event.target instanceof HTMLTextAreaElement)
      ) {
        setCovering(false);
      }
    };
    document.addEventListener("keydown", exit);
    return () => document.removeEventListener("keydown", exit);
  }, [covering]);

  const full = native || covering;
  const toggle = () => {
    if (native) {
      void document.exitFullscreen();
    } else if (covering) {
      setCovering(false);
    } else if (document.fullscreenEnabled && frame.current) {
      frame.current.requestFullscreen().catch(() => setCovering(true));
    } else {
      setCovering(true);
    }
  };
  return [full, toggle] as const;
}

function LiveScreen({
  chatId,
  onStop,
}: {
  chatId: string;
  onStop: () => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const rfb = useRef<RFB | null>(null);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  const [full, toggleFull] = useFullscreen(frame);
  const [lease, setLease] = useState<Control | null>(null);
  const held = Boolean(lease?.held);
  const screen = useScreen(container, rfb, chatId, held, () => {
    setLease(null);
    toast.add({
      description: m.chat_your_control_time_expired_or_the_agent_took(),
      title: m.chat_control_returned_to_the_agent(),
      type: "info",
    });
  });
  const control = useMutation({
    mutationFn: (action: "take" | "release") => setControl(chatId, action),
    onSuccess: setLease,
  });
  const controlButton = (
    <Button
      disabled={control.isPending || screen.state !== "live"}
      onClick={() => control.mutate(held ? "release" : "take")}
      size="sm"
      variant={held ? "default" : "outline"}
    >
      {held ? m.chat_give_back_to_the_agent() : m.chat_take_control()}
    </Button>
  );

  const viewerTools = (
    <>
      {held && (
        <Button
          aria-label={m.chat_show_the_keyboard()}
          className="hidden pointer-coarse:inline-flex"
          onClick={() => keyboard.current?.focus()}
          size="icon-sm"
          variant={full ? "secondary" : "ghost"}
        >
          <IconKeyboard />
        </Button>
      )}
      <Button
        aria-label={full ? m.chat_exit_fullscreen() : m.chat_fullscreen()}
        disabled={!full && screen.state !== "live"}
        onClick={toggleFull}
        size="icon-sm"
        title={full ? m.chat_exit_fullscreen() : m.chat_fullscreen()}
        variant={full ? "secondary" : "ghost"}
      >
        {full ? <IconMinimize /> : <IconMaximize />}
      </Button>
    </>
  );

  useEffect(() => {
    if (!held) {
      keyboard.current?.blur();
    }
  }, [held]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <StatusText tone={held ? "attention" : "live"}>
          {held && lease?.expires_at
            ? m.chat_yours_until({ time: clock(lease.expires_at) })
            : m.chat_watching()}
        </StatusText>
        <div className="ml-auto flex items-center gap-1">
          {controlButton}
          {!full && viewerTools}
          <Button
            aria-label={m.chat_stop_watching()}
            onClick={onStop}
            size="icon-sm"
            title={m.chat_stop_watching()}
            variant="ghost"
          >
            <IconX />
          </Button>
        </div>
      </div>
      {control.isError && (
        <ErrorState
          error={control.error}
          onRetry={() => {
            if (control.variables) {
              control.mutate(control.variables);
            }
          }}
        />
      )}
      {screen.state === "failed" ? (
        <ErrorState error={screen.error} />
      ) : (
        <div
          className={cn(
            "relative overflow-hidden",
            full
              ? "fixed inset-0 z-50 bg-black pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]"
              : "bg-muted aspect-16/10 rounded-md border",
          )}
          ref={frame}
        >
          <div
            aria-label={
              held
                ? m.chat_the_chats_computer_you_control_mouse_and_keyboard()
                : m.chat_the_chats_computer_view_only()
            }
            className="size-full"
            ref={container}
            role="img"
          />
          {screen.state !== "live" && (
            <p
              className="text-muted-foreground absolute inset-0 flex items-center justify-center"
              role="status"
            >
              {SCREEN_NOTE[screen.state]()}
            </p>
          )}
          {held && <RemoteKeyboard field={keyboard} rfb={rfb} />}
          {full && (
            <div className="absolute top-[max(0.5rem,env(safe-area-inset-top))] right-2 flex gap-1.5">
              {controlButton}
              {viewerTools}
            </div>
          )}
        </div>
      )}
      {held && (
        <p className="text-muted-foreground text-xs">
          {m.chat_the_agent_waits_while_you_have_control_leaving()}
        </p>
      )}
    </div>
  );
}

/** Looking starts the Computer, so the screen opens only when asked. */
export function ComputerScreen({
  chatId,
  running,
}: {
  chatId: string;
  running: boolean;
}) {
  const [watching, setWatching] = useState(running);
  if (watching) {
    return <LiveScreen chatId={chatId} onStop={() => setWatching(false)} />;
  }
  return (
    <div className="bg-muted/50 flex aspect-16/10 flex-col items-center justify-center gap-3 rounded-lg p-6 text-center">
      <IconDeviceDesktop
        aria-hidden="true"
        className="text-muted-foreground size-8"
        stroke={1.5}
      />
      <p className="text-muted-foreground max-w-64 text-sm">
        {running
          ? m.chat_the_screen_is_hidden()
          : m.chat_starts_when_the_agent_needs_a_browser_or()}
      </p>
      <Button onClick={() => setWatching(true)} size="sm">
        {running
          ? m.chat_show_the_screen()
          : m.chat_start_and_show_the_screen()}
      </Button>
    </div>
  );
}
