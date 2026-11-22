import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { flatten } from "@/api/query.ts";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { catalogQueries } from "@/features/catalog/api.ts";
import { chatQueries } from "@/features/chats/api.ts";
import { ACTIVE_STATUSES } from "@/features/chats/model.ts";
import { policyQueries } from "@/features/policy/api.ts";
import { RuleEditor } from "@/features/policy/rule-editor.tsx";
import { EMPTY_RULE, fromRule } from "@/features/policy/rule-form.ts";
import { m } from "@/paraglide/messages.js";

/**
 * Loads what the editor needs, then opens it empty (optionally with an action already chosen)
 * or filled from the rule being changed.
 */
export function RuleBuilder({
  ruleId,
  presetTool,
  presetChat,
  onSaved,
  onCancel,
}: {
  ruleId?: string;
  presetTool?: string;
  presetChat?: string;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const tools = useQuery(catalogQueries.tools());
  const rules = useQuery(
    presetChat ? policyQueries.chat(presetChat) : policyQueries.global(),
  );
  const chats = useInfiniteQuery(chatQueries.list(ACTIVE_STATUSES));

  if (tools.isPending || rules.isPending) {
    return <LoadingRows rows={4} />;
  }
  if (tools.isError || rules.isError) {
    const failed = tools.isError ? tools : rules;
    return (
      <ErrorState error={failed.error} onRetry={() => void failed.refetch()} />
    );
  }
  const rule = ruleId
    ? rules.data.find((item) => item.id === ruleId)
    : undefined;
  if (ruleId && !rule) {
    return (
      <EmptyState title={m.rule_this_rule_no_longer_exists()}>
        {m.rule_it_may_have_been_deleted_here_or_in()}
        <div className="mt-3">
          {onCancel ? (
            <Button onClick={onCancel} variant="outline">
              {m.rule_back()}
            </Button>
          ) : (
            <Button
              nativeButton={false}
              render={<Link to="/guardrails" />}
              variant="outline"
            >
              {m.rule_back_to_your_rules()}
            </Button>
          )}
        </div>
      </EmptyState>
    );
  }
  return (
    <RuleEditor
      chats={flatten(chats.data)}
      fixedChat={presetChat !== undefined}
      initial={
        rule
          ? fromRule(rule, tools.data)
          : {
              ...EMPTY_RULE,
              ...(presetChat
                ? { chatId: presetChat, scope: "chat" as const }
                : {}),
              tool: tools.data.some((tool) => tool.name === presetTool)
                ? (presetTool ?? "")
                : "",
            }
      }
      key={ruleId ?? "new"}
      onCancel={onCancel}
      onSaved={onSaved}
      rule={rule}
      rules={rules.data}
      tools={tools.data}
    />
  );
}
