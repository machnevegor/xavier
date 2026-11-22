import { IconChevronRight } from "@tabler/icons-react";
import type { ReactNode } from "react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
/** A quiet "more" link: secondary detail that stays one click away. */
export function Disclosure({
  label,
  defaultOpen = false,
  className,
  contentClassName,
  children,
}: {
  label: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}) {
  return (
    <Collapsible className={className} defaultOpen={defaultOpen}>
      <CollapsibleTrigger className="group text-muted-foreground hover:text-foreground inline-flex min-h-9 items-center gap-1 rounded text-sm">
        <IconChevronRight
          aria-hidden="true"
          className="size-3.5 transition-transform group-data-[panel-open]:rotate-90"
        />
        {label}
      </CollapsibleTrigger>
      <CollapsibleContent className={contentClassName}>
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
