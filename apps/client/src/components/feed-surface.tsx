import { cn } from "cn";
import type { ReactNode } from "react";

/**
 * Machine-readable output inset: muted surface on the page background, not nested cards.
 * Matches the agent-control pattern in docs/web-ui.md — results stay visible; records
 * open on demand.
 */
export function FeedRecord({
  label,
  children,
  className,
}: {
  label?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-label={label}
      className={cn(
        "bg-muted/50 border-border/80 space-y-2 rounded-md border px-3 py-2.5",
        className,
      )}
    >
      {label ? (
        <h4 className="text-muted-foreground text-xs font-medium">{label}</h4>
      ) : null}
      {children}
    </section>
  );
}

/** Labelled control for operator input on decision cards — same weight as question options. */
export function FeedComposerField({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint ? (
        <p className="text-muted-foreground text-xs leading-relaxed">{hint}</p>
      ) : null}
    </div>
  );
}

/** Why the agent asked — prose, not decorative quotes. */
export function AgentReason({ children }: { children: string }) {
  return (
    <p className="text-muted-foreground border-border mt-2 border-l-2 py-0.5 pl-3 text-sm leading-relaxed">
      {children}
    </p>
  );
}
