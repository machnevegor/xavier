import { cn } from "cn";
import type { ReactNode } from "react";

const TONE = {
  attention: "text-attention",
  muted: "text-muted-foreground",
  ok: "text-ok",
} as const;

/**
 * The frame every request of the agent shares: what kind of request, in a coloured line with
 * its age, the request itself, then the answers in a footer. Settled requests drop the footer
 * and shrink to a summary.
 */
export function DecisionCard({
  label,
  icon,
  tone = "attention",
  meta,
  emphasis = false,
  busy,
  ariaLabel,
  footer,
  className,
  children,
}: {
  label: ReactNode;
  icon: ReactNode;
  tone?: keyof typeof TONE;
  meta?: ReactNode;
  /** Outlines the card in the attention colour, for sensitive requests. */
  emphasis?: boolean;
  busy?: boolean;
  ariaLabel: string;
  footer?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section
      aria-busy={busy}
      aria-label={ariaLabel}
      className={cn(
        "bg-card animate-in fade-in-0 slide-in-from-bottom-1 overflow-hidden rounded-lg border duration-200",
        emphasis && "border-attention",
        className,
      )}
    >
      <div className="flex items-center gap-2 px-3 pt-2.5 text-xs">
        <span
          className={cn(
            "flex min-w-0 items-center gap-1.5 font-medium [&_svg]:size-3.5 [&_svg]:shrink-0",
            TONE[tone],
          )}
        >
          {icon}
          {label}
        </span>
        {meta && (
          <span className="text-muted-foreground ml-auto shrink-0">{meta}</span>
        )}
      </div>
      {children && <div className="px-3 pt-1.5 pb-3">{children}</div>}
      {footer && (
        <div className="bg-muted/30 flex flex-wrap items-center gap-2 border-t px-3 py-2.5">
          {footer}
        </div>
      )}
    </section>
  );
}
