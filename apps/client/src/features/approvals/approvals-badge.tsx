import { useInfiniteQuery } from "@tanstack/react-query";
import { flatten } from "@/api/query.ts";
import { approvalQueries } from "@/features/approvals/api.ts";
import { m } from "@/paraglide/messages.js";

export function ApprovalsBadge() {
  const { data, hasNextPage } = useInfiniteQuery(approvalQueries.waiting());
  const count = flatten(data).length;
  if (count === 0) {
    return null;
  }
  return (
    <span className="text-attention text-xs font-medium tabular-nums">
      <span aria-hidden="true">
        {count}
        {hasNextPage ? "+" : ""}
      </span>
      <span className="sr-only">
        {m.nav_waiting({ count })}
        {hasNextPage ? "+" : ""}
      </span>
    </span>
  );
}
