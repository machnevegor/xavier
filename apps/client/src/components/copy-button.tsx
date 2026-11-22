import { IconCheck, IconCopy } from "@tabler/icons-react";
import { cn } from "cn";
import { type ReactNode, useEffect, useState } from "react";
import { Button } from "@/components/ui/button.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { copyText } from "@/lib/clipboard.ts";
import { m } from "@/paraglide/messages.js";

const FEEDBACK_MS = 1600;

/** Copies the complete original text, whatever part of it is on screen. */
export function CopyButton({
  value,
  label = m.common_copy(),
  className,
  children,
}: {
  /** The text, or a function that builds it when the button is pressed. */
  value: string | (() => string);
  label?: string;
  className?: string;
  children?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) {
      return;
    }
    const timeout = setTimeout(() => setCopied(false), FEEDBACK_MS);
    return () => clearTimeout(timeout);
  }, [copied]);

  const copy = () => {
    copyText(typeof value === "string" ? value : value())
      .then(() => setCopied(true))
      .catch(() =>
        toast.add({
          title: m.common_cant_copy_in_this_browser_window(),
          type: "error",
        }),
      );
  };

  return (
    <Button
      aria-label={copied ? m.common_copied() : label}
      className={cn("text-muted-foreground", className)}
      onClick={copy}
      size={children ? "xs" : "icon-xs"}
      title={copied ? m.common_copied() : label}
      variant="ghost"
    >
      {copied ? <IconCheck /> : <IconCopy />}
      {children}
    </Button>
  );
}
