import { useInfiniteQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo } from "react";
import { flatten } from "@/api/query.ts";
import { Page, PageHeader } from "@/components/page.tsx";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadMore,
} from "@/components/states.tsx";
import { ActionRequestCard } from "@/features/approvals/action-request-card.tsx";
import { approvalQueries } from "@/features/approvals/api.ts";
import { chatQueries } from "@/features/chats/api.ts";
import { ACTIVE_STATUSES } from "@/features/chats/model.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/approvals")({
  component: ApprovalsPage,
  head: () => ({ meta: [{ title: m.approvals_needs_you_xavier() }] }),
});

function ApprovalsPage() {
  const approvals = useInfiniteQuery(approvalQueries.waiting());
  const chats = useInfiniteQuery(chatQueries.list(ACTIVE_STATUSES));
  const titles = useMemo(
    () => new Map(flatten(chats.data).map((chat) => [chat.id, chat.title])),
    [chats.data],
  );
  const items = flatten(approvals.data);

  return (
    <Page className="max-w-3xl">
      <PageHeader
        lead={m.approvals_actions_the_agent_wont_take_without_your_decision()}
        title={m.approvals_needs_you()}
      />
      {approvals.isPending ? (
        <LoadingRows />
      ) : approvals.isError ? (
        <ErrorState
          error={approvals.error}
          onRetry={() => void approvals.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState title={m.approvals_no_requests_are_waiting()}>
          {m.approvals_when_guardrails_need_your_decision_the_request_appears()}
        </EmptyState>
      ) : (
        <>
          <ul className="space-y-4">
            {items.map((action) => (
              <li key={action.id}>
                <ActionRequestCard
                  action={action}
                  chatTitle={
                    titles.get(action.chat_id) ?? m.approval_the_chat()
                  }
                />
              </li>
            ))}
          </ul>
          <LoadMore {...approvals} />
        </>
      )}
    </Page>
  );
}
