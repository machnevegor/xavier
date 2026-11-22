import {
  IconPencil,
  IconPlus,
  IconShield,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { explain } from "@/api/errors.ts";
import type { Chat, PolicyRule } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { ProfileEmpty } from "@/features/chats/profile-sections.tsx";
import { deleteRule, policyQueries } from "@/features/policy/api.ts";
import { EFFECT } from "@/features/policy/model.ts";
import { RuleBuilder } from "@/features/policy/rule-builder.tsx";
import { describeRule, precedence } from "@/features/policy/semantics.ts";
import { m } from "@/paraglide/messages.js";

function RuleRow({
  rule,
  chat,
  onEdit,
}: {
  rule: PolicyRule;
  chat: Chat;
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => deleteRule(rule.id),
    onError: (error) => {
      const { title, hint } = explain(error);
      toast.add({ description: hint, timeout: 0, title, type: "error" });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["rules"] }),
  });
  const sentence = describeRule(rule, chat.title);
  return (
    <li className="flex items-start gap-1 py-2.5">
      <div className="min-w-0 flex-1">
        <StatusText className="text-xs" tone={EFFECT[rule.effect].tone}>
          {EFFECT[rule.effect].label()}
        </StatusText>
        <p className="mt-0.5 text-sm break-words">{sentence}</p>
        {rule.description && (
          <p className="text-muted-foreground text-xs">{rule.description}</p>
        )}
      </div>
      <Button
        aria-label={m.chat_edit_rule({ sentence })}
        className="text-muted-foreground"
        onClick={onEdit}
        size="icon-sm"
        variant="ghost"
      >
        <IconPencil />
      </Button>
      <ConfirmAction
        confirmLabel={m.chat_delete_rule_2()}
        description={
          rule.effect === "allow"
            ? m.chat_the_agent_goes_back_to_what_other_rules()
            : m.chat_actions_it_covered_fall_back_to_other_rules()
        }
        onConfirm={() => remove.mutate()}
        title={m.chat_delete_this_rule()}
        trigger={
          <Button
            aria-label={m.chat_delete_rule({ sentence })}
            className="text-muted-foreground"
            disabled={remove.isPending}
            size="icon-sm"
            variant="ghost"
          >
            <IconTrash />
          </Button>
        }
      />
    </li>
  );
}

/**
 * Rules that apply only to this chat, including those saved by "Always allow" for this chat.
 * Rules for every chat stay on the Guardrails page.
 */
export function ChatGuardrails({ chat }: { chat: Chat }) {
  const rules = useQuery(policyQueries.chat(chat.id));
  const [editing, setEditing] = useState<{ ruleId?: string } | null>(null);
  if (rules.isPending) {
    return <LoadingRows rows={2} />;
  }
  if (rules.isError) {
    return (
      <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
    );
  }
  const own = [...rules.data].sort(precedence);
  const close = () => setEditing(null);

  if (editing) {
    return (
      <div className="space-y-4">
        <div>
          <h3 className="font-medium">
            {editing.ruleId
              ? m.chat_change_rule()
              : m.chat_new_rule_for_this_chat()}
          </h3>
          <p className="text-muted-foreground mt-1 text-sm">
            {m.chat_it_applies_only_to_this_chat_anything_it()}
          </p>
        </div>
        <RuleBuilder
          onCancel={close}
          onSaved={() => {
            close();
            toast.add({ title: m.chat_rule_saved(), type: "success" });
          }}
          presetChat={chat.id}
          ruleId={editing.ruleId}
        />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {own.length === 0 ? (
        <ProfileEmpty icon={IconShield} title={m.chat_no_rules_for_this_chat()}>
          {m.chat_the_chats_mode_decides_what_needs_your_approval()}
        </ProfileEmpty>
      ) : (
        <ul aria-label={m.chat_rules_of_this_chat()} className="divide-y">
          {own.map((rule) => (
            <RuleRow
              chat={chat}
              key={rule.id}
              onEdit={() => setEditing({ ruleId: rule.id })}
              rule={rule}
            />
          ))}
        </ul>
      )}
      <Button
        className="w-full"
        onClick={() => setEditing({})}
        size="sm"
        variant="outline"
      >
        <IconPlus data-icon="inline-start" />
        {m.chat_add_rule_for_this_chat()}
      </Button>
      <p className="text-muted-foreground text-xs">
        {m.chat_rules_for_every_chat_live_in_guardrails()}{" "}
        <Link className="underline underline-offset-4" to="/guardrails">
          {m.chat_open_guardrails()}
        </Link>
      </p>
    </div>
  );
}
