import { IconPencil, IconTrash } from "@tabler/icons-react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { flatten } from "@/api/query.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { chatQueries } from "@/features/chats/api.ts";
import { ACTIVE_STATUSES } from "@/features/chats/model.ts";
import { deleteRule, policyQueries } from "@/features/policy/api.ts";
import { EFFECT } from "@/features/policy/model.ts";
import { describeRule, precedence } from "@/features/policy/semantics.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/**
 * Each rule as a sentence, in the order Xavier ranks them, so the first rule that matches a
 * call is the one that decides it. The technical form sits underneath.
 */
export function RuleList() {
  const queryClient = useQueryClient();
  const rules = useQuery(policyQueries.global());
  const chats = useInfiniteQuery(chatQueries.list(ACTIVE_STATUSES));
  const remove = useMutation({
    mutationFn: deleteRule,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["rules"] }),
  });
  const titles = new Map(
    flatten(chats.data).map((chat) => [chat.id, chat.title]),
  );

  if (rules.isPending) {
    return <LoadingRows />;
  }
  if (rules.isError) {
    return (
      <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
    );
  }
  const sorted = [...rules.data].sort(precedence);
  return (
    <>
      {remove.isError && <ErrorState error={remove.error} />}
      {sorted.length === 0 ? (
        <EmptyState title={m.guardrails_no_rules_yet()}>
          {m.guardrails_each_chats_mode_decides_everything_add_a_rule()}
        </EmptyState>
      ) : (
        <ul className="divide-y border-y">
          {sorted.map((rule) => {
            const sentence = describeRule(
              rule,
              rule.chat_id ? titles.get(rule.chat_id) : undefined,
            );
            return (
              <li className="flex items-start gap-1 py-4" key={rule.id}>
                <div className="min-w-0 flex-1">
                  <StatusText tone={EFFECT[rule.effect].tone}>
                    {EFFECT[rule.effect].label()}
                  </StatusText>
                  <p className="mt-1 font-medium break-words">{sentence}</p>
                  {rule.description && (
                    <p className="text-muted-foreground mt-0.5">
                      {rule.description}
                    </p>
                  )}
                  <p className="text-muted-foreground mt-1 font-mono text-xs break-words">
                    {rule.tool_pattern} /{" "}
                    {rule.resource_pattern ?? m.guardrails_no_place()}{" "}
                    {m.guardrails_priority({ n: rule.priority })}
                    <span className="font-sans">
                      {" "}
                      {m.guardrails_added({ ago: timeAgo(rule.created_at) })}
                    </span>
                  </p>
                </div>
                <Button
                  aria-label={m.guardrails_edit_rule({ sentence })}
                  className="size-11 shrink-0"
                  nativeButton={false}
                  render={
                    <Link
                      params={{ ruleId: rule.id }}
                      to="/guardrails/$ruleId"
                    />
                  }
                  size="icon"
                  variant="ghost"
                >
                  <IconPencil />
                </Button>
                <ConfirmAction
                  confirmLabel={m.guardrails_delete_rule_2()}
                  description={
                    rule.effect === "allow"
                      ? m.guardrails_the_agent_will_go_back_to_what_other()
                      : m.guardrails_actions_it_covered_fall_back_to_other_rules()
                  }
                  onConfirm={() => remove.mutate(rule.id)}
                  title={m.guardrails_delete_this_rule()}
                  trigger={
                    <Button
                      aria-label={m.guardrails_delete_rule({ sentence })}
                      className="size-11 shrink-0"
                      disabled={remove.isPending}
                      size="icon"
                      variant="ghost"
                    >
                      <IconTrash />
                    </Button>
                  }
                />
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
