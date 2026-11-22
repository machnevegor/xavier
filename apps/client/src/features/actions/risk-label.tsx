import { cn } from "cn";
import type { Risk } from "@/api/types.ts";
import { RISK } from "@/features/actions/model.ts";

const STYLE: Record<Risk, string> = {
  forbidden: "text-destructive",
  read: "text-muted-foreground",
  sensitive: "text-attention",
  write: "text-foreground",
};

/** Risk is a word; colour is added only where the risk itself calls for care. */
export function RiskLabel({ risk }: { risk: Risk }) {
  return (
    <span
      className={cn("text-xs font-medium whitespace-nowrap", STYLE[risk])}
      title={RISK[risk].hint()}
    >
      {RISK[risk].label()}
    </span>
  );
}
