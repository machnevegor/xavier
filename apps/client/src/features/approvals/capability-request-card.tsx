import { IconLockOpen } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type Action, isRecord } from "@/api/types.ts";
import { AgentReason } from "@/components/feed-surface.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { decide } from "@/features/approvals/api.ts";
import { DecisionCard } from "@/features/approvals/decision-card.tsx";
import { TOOL_GROUPS } from "@/features/chats/model.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const OTHER: Record<string, { label: () => string; hint: () => string }> = {
  execute_network: {
    hint: m.approval_code_it_runs_may_reach_the_internet,
    label: m.approval_network_for_code,
  },
  proactive: {
    hint: m.approval_wake_itself_up_later_and_follow_events,
    label: m.approval_proactive,
  },
};

/**
 * Approval card for `request_capability`: turn it on for this chat, or decline and have the
 * refusal remembered until settings clear it.
 */
export function CapabilityRequestCard({
  action,
  chatTitle,
}: {
  action: Action;
  chatTitle?: string;
}) {
  const args = isRecord(action.arguments) ? action.arguments : {};
  const capability = typeof args.capability === "string" ? args.capability : "";
  const reason = typeof args.reason === "string" ? args.reason : "";
  const known =
    TOOL_GROUPS.find((item) => item.value === capability) ?? OTHER[capability];
  const name = known
    ? known.label()
    : capability || m.approval_asks_for_more_access();
  const queryClient = useQueryClient();
  const settle = () =>
    void queryClient.invalidateQueries({
      predicate: ({ queryKey }) =>
        queryKey[0] === "approvals" ||
        queryKey[0] === "chats" ||
        (queryKey[0] === "chat" && queryKey[1] === action.chat_id),
    });
  const approve = useMutation({
    mutationFn: () => decide(action.id, "approve"),
    onSettled: settle,
  });
  const reject = useMutation({
    mutationFn: () => decide(action.id, "reject"),
    onSettled: settle,
  });
  const busy = approve.isPending || reject.isPending;

  return (
    <DecisionCard
      ariaLabel={m.approval_capability_request({ name })}
      busy={busy}
      footer={
        <>
          <Button
            className="h-11 sm:h-8"
            disabled={busy}
            onClick={() => approve.mutate()}
            size="sm"
          >
            {approve.isPending
              ? m.approval_turning_on()
              : m.approval_turn_on_for_this_chat()}
          </Button>
          <Button
            className="h-11 sm:h-8"
            disabled={busy}
            onClick={() => reject.mutate()}
            size="sm"
            variant="outline"
          >
            {reject.isPending ? m.approval_declining() : m.approval_decline()}
          </Button>
        </>
      }
      icon={<IconLockOpen aria-hidden="true" />}
      label={m.approval_asks_for_more_access()}
      meta={timeAgo(action.created_at)}
    >
      <p className="text-base">
        <span className="font-medium">{name}</span>
        {known && (
          <span className="text-muted-foreground"> — {known.hint()}</span>
        )}
      </p>
      {reason && <AgentReason>{reason}</AgentReason>}
      <p className="text-muted-foreground mt-1.5 text-xs">
        {m.approval_only_this_chat_gets_it_its_mode_still()}
        {chatTitle && (
          <>
            {" "}
            <Link
              className="underline underline-offset-2"
              params={{ chatId: action.chat_id }}
              to="/chats/$chatId"
            >
              {m.approval_open({ chat: chatTitle })}
            </Link>
          </>
        )}
      </p>
      {(approve.isError || reject.isError) && (
        <div className="mt-3">
          <ErrorState error={approve.error ?? reject.error} />
        </div>
      )}
    </DecisionCard>
  );
}
