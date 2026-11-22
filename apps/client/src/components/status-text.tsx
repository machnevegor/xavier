import { IconCheck, IconX } from "@tabler/icons-react";
import { cn } from "cn";
import type { ReactNode } from "react";

export type Tone = "neutral" | "live" | "ok" | "attention" | "danger";

export interface StatusMeta {
  label: string;
  tone: Tone;
  pulse?: boolean;
}

const TEXT: Record<Tone, string> = {
  attention: "text-attention",
  danger: "text-destructive",
  live: "text-foreground",
  neutral: "text-muted-foreground",
  ok: "text-ok",
};

const MARK: Record<Tone, string | null> = {
  attention: "size-1.5 rotate-45 bg-attention",
  danger: null,
  live: "size-2 rounded-full bg-ok",
  neutral: "size-2 rounded-full border border-muted-foreground",
  ok: null,
};

/**
 * Status is words first. Each meaning has its own mark shape, so states stay apart without
 * colour: ring idle, dot in progress, check done, diamond needs a person, cross failed.
 */
export function StatusText({
  tone = "neutral",
  pulse = false,
  className,
  children,
}: {
  tone?: Tone;
  pulse?: boolean | undefined;
  className?: string;
  children: ReactNode;
}) {
  const mark = MARK[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-sm whitespace-nowrap",
        TEXT[tone],
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-3.5 shrink-0 items-center justify-center"
      >
        {tone === "ok" && <IconCheck className="size-3.5" stroke={2.5} />}
        {tone === "danger" && <IconX className="size-3.5" stroke={2.5} />}
        {mark && (
          <span className={cn(mark, pulse && "motion-safe:animate-pulse")} />
        )}
      </span>
      {children}
    </span>
  );
}

export function Status({ meta }: { meta: StatusMeta }) {
  return (
    <StatusText pulse={meta.pulse} tone={meta.tone}>
      {meta.label}
    </StatusText>
  );
}
