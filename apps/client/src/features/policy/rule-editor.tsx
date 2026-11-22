import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { cn } from "cn";
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Chat, Effect, PolicyRule, Tool } from "@/api/types.ts";
import { ChoiceGroup } from "@/components/choice-group.tsx";
import { FormField } from "@/components/form-field.tsx";
import { ErrorState } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Checkbox } from "@/components/ui/checkbox.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { RISK } from "@/features/actions/model.ts";
import { toolConsequence, toolLabel } from "@/features/catalog/model.ts";
import { modeLabel } from "@/features/chats/model.ts";
import { saveRule } from "@/features/policy/api.ts";
import { EFFECT } from "@/features/policy/model.ts";
import {
  CUSTOM,
  firstProblem,
  type RuleErrors,
  type RuleForm,
  toDraft,
  validateRule,
} from "@/features/policy/rule-form.ts";
import {
  describeRule,
  type PreviewRow,
  previewRule,
  type RuleLike,
  ruleInteractions,
  ruleWarnings,
  sameTarget,
} from "@/features/policy/semantics.ts";
import { m } from "@/paraglide/messages.js";

const EFFECTS = (["ask", "allow", "deny"] as const).map((value) => ({
  hint: EFFECT[value].hint,
  label: EFFECT[value].label,
  value,
}));

const WHERE = [
  {
    hint: m.rule_every_use_of_the_action_whatever_website_address,
    label: m.rule_anywhere_2,
    value: "anywhere",
  },
  {
    hint: m.rule_a_website_a_web_address_or_a_file,
    label: m.rule_only_in_one_place,
    value: "specific",
  },
] as const;

const SCOPES = [
  {
    hint: m.rule_every_chat_you_have_now_and_start_later,
    label: m.rule_all_chats,
    value: "all",
  },
  {
    hint: m.rule_only_the_chat_you_choose_other_chats_are,
    label: m.rule_one_chat,
    value: "chat",
  },
] as const;

const spoken = <T extends string>(
  options: readonly { value: T; label: () => string; hint: () => string }[],
) =>
  options.map((option) => ({
    hint: option.hint(),
    label: option.label(),
    value: option.value,
  }));

type SetForm = (patch: Partial<RuleForm>) => void;
type Describe = (rule: RuleLike) => string;

function TargetFields({
  form,
  set,
  errors,
  tools,
}: {
  form: RuleForm;
  set: SetForm;
  errors: RuleErrors;
  tools: readonly Tool[];
}) {
  const chosen = tools.find((tool) => tool.name === form.tool);
  const items = [
    ...tools.map((tool) => ({ label: toolLabel(tool.name), value: tool.name })),
    { label: m.rule_any_action_2(), value: "*" },
    { label: m.rule_a_name_or_pattern_i_type(), value: CUSTOM },
  ];
  return (
    <>
      <FormField
        error={form.tool === CUSTOM ? undefined : errors.tool}
        help={
          chosen
            ? m.rule_text_2({
                consequences: toolConsequence(chosen.name, chosen.consequences),
                "risk hint": RISK[chosen.risk].hint(),
              })
            : m.rule_pick_what_the_agent_would_be_doing()
        }
        id="rule-tool"
        label={m.rule_which_action()}
      >
        <Select
          items={items}
          onValueChange={(tool) => set({ tool: tool ?? "" })}
          value={form.tool || null}
        >
          <SelectTrigger
            aria-invalid={Boolean(errors.tool)}
            className="w-full"
            id="rule-tool"
          >
            <SelectValue placeholder={m.rule_choose_an_action()} />
          </SelectTrigger>
          <SelectContent>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormField>
      {form.tool === CUSTOM && (
        <FormField
          error={errors.tool}
          help={m.rule_the_technical_name_such_as_browser_stands_for()}
          id="rule-tool-pattern"
          label={m.rule_action_name_or_pattern()}
        >
          <Input
            aria-invalid={Boolean(errors.tool)}
            autoCapitalize="none"
            className="font-mono"
            id="rule-tool-pattern"
            onChange={(event) => set({ customTool: event.target.value })}
            spellCheck={false}
            value={form.customTool}
          />
        </FormField>
      )}
      <ChoiceGroup
        label={m.rule_where()}
        onChange={(where) => set({ where })}
        options={spoken(WHERE)}
        value={form.where}
      />
      {form.where === "specific" && (
        <FormField
          error={errors.resource}
          help={m.rule_compared_with_each_website_full_address_and_file()}
          id="rule-resource"
          label={m.rule_website_address_or_file()}
        >
          <Input
            aria-invalid={Boolean(errors.resource)}
            autoCapitalize="none"
            className="font-mono"
            id="rule-resource"
            onChange={(event) => set({ resource: event.target.value })}
            spellCheck={false}
            value={form.resource}
          />
        </FormField>
      )}
    </>
  );
}

/** A rule saved for a chat that is no longer open keeps pointing at it until another is chosen. */
function ChatField({
  form,
  set,
  error,
  chats,
}: {
  form: RuleForm;
  set: SetForm;
  error: string | undefined;
  chats: readonly Chat[];
}) {
  const missing =
    form.chatId !== "" && !chats.some((chat) => chat.id === form.chatId);
  const items = [
    ...(missing
      ? [{ label: m.rule_a_chat_that_was_deleted(), value: form.chatId }]
      : []),
    ...chats.map((chat) => ({ label: chat.title, value: chat.id })),
  ];
  return (
    <>
      <ChoiceGroup
        label={m.rule_in_which_chats()}
        onChange={(scope) => set({ scope })}
        options={spoken(SCOPES)}
        value={form.scope}
      />
      {form.scope === "chat" && (
        <FormField
          error={error}
          help={
            chats.length > 0
              ? undefined
              : m.rule_you_have_no_open_chats_to_choose_from()
          }
          id="rule-chat"
          label={m.rule_chat()}
        >
          <Select
            items={items}
            onValueChange={(chatId) => set({ chatId: chatId ?? "" })}
            value={form.chatId || null}
          >
            <SelectTrigger
              aria-invalid={Boolean(error)}
              className="w-full"
              id="rule-chat"
            >
              <SelectValue placeholder={m.rule_choose_a_chat()} />
            </SelectTrigger>
            <SelectContent>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormField>
      )}
    </>
  );
}

/** Settings most rules never need. The editor opens it when one of them has a problem. */
function Advanced({
  form,
  set,
  errors,
  open,
  onOpenChange,
}: {
  form: RuleForm;
  set: SetForm;
  errors: RuleErrors;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const draft = toDraft(form);
  return (
    <Collapsible onOpenChange={onOpenChange} open={open}>
      <CollapsibleTrigger className="inline-flex min-h-11 items-center underline underline-offset-2">
        {m.rule_advanced_settings()}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-5">
        <FormField
          error={errors.priority}
          help={m.rule_decides_between_rules_that_cover_the_same_action()}
          id="rule-priority"
          label={m.rule_priority()}
        >
          <Input
            aria-invalid={Boolean(errors.priority)}
            className="max-w-32"
            id="rule-priority"
            inputMode="numeric"
            onChange={(event) => set({ priority: event.target.value })}
            value={form.priority}
          />
        </FormField>
        <FormField
          help={m.rule_a_reminder_of_why_this_rule_exists_shown()}
          id="rule-note"
          label={m.rule_note_optional()}
        >
          <Input
            id="rule-note"
            onChange={(event) => set({ description: event.target.value })}
            value={form.description}
          />
        </FormField>
        {draft.tool_pattern && (
          <p className="text-muted-foreground break-words">
            {m.rule_saved_as_action_place_effect({
              effect: draft.effect,
              place: draft.resource_pattern ?? m.rule_none(),
              tool: draft.tool_pattern,
            })}
          </p>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

function Change({
  row,
  describe,
}: {
  row: PreviewRow["modes"][number];
  describe: Describe;
}) {
  if (row.overruledBy) {
    return (
      <span className="text-muted-foreground">
        {m.rule_no_change_your_rule_still_wins({
          effect: EFFECT[row.after].label(),
          rule: describe(row.overruledBy),
        })}
      </span>
    );
  }
  if (row.before === row.after) {
    return (
      <span className="text-muted-foreground">
        {m.rule_as_it_is_now({ effect: EFFECT[row.after].label() })}
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-x-2">
      <span className="text-muted-foreground">
        {EFFECT[row.before].label()} →
      </span>
      <StatusText tone={EFFECT[row.after].tone}>
        {EFFECT[row.after].label()}
      </StatusText>
    </span>
  );
}

/** What changes for each covered tool in each chat mode; folded away when it covers many. */
function Effects({
  rows,
  describe,
}: {
  rows: PreviewRow[];
  describe: Describe;
}) {
  if (rows.length === 0) {
    return null;
  }
  const list = (
    <ul className="divide-y border-y">
      {rows.map((row) => (
        <li className="py-3" key={`${row.tool.name}:${row.risk}`}>
          <p className="font-medium">
            {toolLabel(row.tool.name)}
            {row.when && (
              <span className="font-normal">
                {m.rule_when({ when: row.when })}
              </span>
            )}{" "}
            <span className="text-muted-foreground font-mono text-xs font-normal">
              {row.tool.name}
            </span>
          </p>
          {row.risk === "forbidden" ? (
            <p className="text-muted-foreground">
              Always blocked. No rule can change that.
            </p>
          ) : row.neverApplies ? (
            <p className="text-muted-foreground">
              Not affected: this action is not tied to a website or file.
            </p>
          ) : (
            <dl className="mt-1 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1">
              {row.modes.map((mode) => (
                <div className="contents" key={mode.mode}>
                  <dt className="text-muted-foreground">
                    {modeLabel(mode.mode)}
                  </dt>
                  <dd>
                    <Change describe={describe} row={mode} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ul>
  );
  if (rows.length <= 2) {
    return list;
  }
  return (
    <Collapsible>
      <CollapsibleTrigger className="inline-flex min-h-11 items-center underline underline-offset-2">
        {m.rule_effect_on_each_of_the_cases_it_covers({ n: rows.length })}
      </CollapsibleTrigger>
      <CollapsibleContent>{list}</CollapsibleContent>
    </Collapsible>
  );
}

function Others({
  title,
  items,
  describe,
}: {
  title: string;
  items: { rule: PolicyRule; why: string }[];
  describe: Describe;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div>
      <p className="font-medium">{title}</p>
      <ul className="text-muted-foreground mt-1 list-disc space-y-1 pl-5">
        {items.map(({ rule, why }) => (
          <li key={rule.id}>
            {describe(rule)} <span className="whitespace-nowrap">({why})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The consequences of a draft before it is saved: one sentence, anything that deserves a
 * second look, how it sits with your other rules, and the effect on each tool.
 */
function Preview({
  draft,
  rules,
  tools,
  editingId,
  describe,
  acknowledged,
  onAcknowledge,
}: {
  draft: RuleLike;
  rules: PolicyRule[];
  tools: readonly Tool[];
  editingId: string | undefined;
  describe: Describe;
  acknowledged: boolean;
  onAcknowledge: (acknowledged: boolean) => void;
}) {
  const rows = previewRule(draft, rules, tools, editingId);
  const { yieldsTo, overrides } = ruleInteractions(
    draft,
    rules,
    tools,
    editingId,
  );
  const twin = rules.some(
    (rule) =>
      rule.id !== editingId &&
      sameTarget(rule, draft) &&
      rule.effect === draft.effect,
  );
  const warnings = [
    ...ruleWarnings(draft, tools),
    ...(twin
      ? [
          {
            level: "caution" as const,
            text: m.rule_you_already_have_a_rule_for_the_same(),
          },
        ]
      : []),
  ];
  const danger = warnings.some((warning) => warning.level === "danger");

  return (
    <section
      aria-labelledby="preview-heading"
      className="space-y-4 border-y py-5"
    >
      <div aria-live="polite">
        <h2 className="type-section" id="preview-heading">
          What this rule will do
        </h2>
        <p className="mt-1 text-base">{describe(draft)}</p>
      </div>
      {warnings.map((warning) => (
        <p
          className={cn(
            "border-l-2 py-0.5 pl-4",
            warning.level === "danger"
              ? "border-destructive"
              : "border-attention",
          )}
          key={warning.text}
        >
          <span className="font-medium">
            {warning.level === "danger" ? m.rule_risky() : m.rule_check()}{" "}
          </span>
          {warning.text}
        </p>
      ))}
      <Others
        describe={describe}
        items={yieldsTo}
        title={m.rule_it_will_not_apply_where_these_rules_win()}
      />
      <Others
        describe={describe}
        items={overrides}
        title={m.rule_it_will_take_over_from_these_rules_where()}
      />
      <Effects describe={describe} rows={rows} />
      <p className="text-muted-foreground text-xs">
        {m.rule_worked_out_in_this_browser_with_xaviers_own()}
      </p>
      {danger && (
        <label
          className="flex min-h-11 cursor-pointer items-start gap-3 py-2"
          htmlFor="rule-ack"
        >
          <Checkbox
            checked={acknowledged}
            className="mt-0.5"
            id="rule-ack"
            onCheckedChange={(checked) => onAcknowledge(checked)}
          />
          <span>{m.rule_i_understand_the_agent_will_not_ask_me()}</span>
        </label>
      )}
    </section>
  );
}

/**
 * A rule as four plain questions, with its consequences shown before saving. Used to add a
 * rule and to change one; changing replaces the stored rule in place.
 */
export function RuleEditor({
  rule,
  initial,
  tools,
  rules,
  chats,
  fixedChat = false,
  onSaved,
  onCancel,
}: {
  rule?: PolicyRule;
  initial: RuleForm;
  tools: Tool[];
  rules: PolicyRule[];
  chats: Chat[];
  /** The chat is already chosen, so the form does not ask which chat the rule belongs to. */
  fixedChat?: boolean;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(initial);
  const [attempted, setAttempted] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [focusId, setFocusId] = useState<{ id: string } | null>(null);
  const set: SetForm = (patch) =>
    setForm((current) => ({ ...current, acknowledged: false, ...patch }));
  const saving = useRef(false);
  const draft = toDraft(form);
  const save = useMutation({
    mutationFn: () => saveRule({ id: rule?.id, input: draft }),
    onSettled: () => {
      saving.current = false;
      void queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
    onSuccess: onSaved,
  });
  /** One save at a time, even for two taps that land before the button disables. */
  const send = () => {
    if (saving.current) {
      return;
    }
    saving.current = true;
    save.mutate();
  };

  const found = validateRule(form);
  const errors = attempted ? found : {};
  const invalid = Object.keys(found).length > 0;
  const titles = new Map(chats.map((chat) => [chat.id, chat.title]));
  const describe: Describe = (item) =>
    describeRule(item, item.chat_id ? titles.get(item.chat_id) : undefined);
  const needsAck =
    !invalid &&
    ruleWarnings(draft, tools).some((warning) => warning.level === "danger") &&
    !form.acknowledged;

  // After a refused save, focus goes to the first answer that needs attention.
  useEffect(() => {
    if (focusId) {
      document.getElementById(focusId.id)?.focus();
    }
  }, [focusId]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    if (found.priority) {
      setAdvancedOpen(true);
    }
    const target = firstProblem(found, form) ?? (needsAck ? "rule-ack" : null);
    if (target) {
      setFocusId({ id: target });
      return;
    }
    send();
  };

  return (
    <form className="space-y-8" noValidate onSubmit={submit}>
      <ChoiceGroup<Effect>
        label={m.rule_what_should_happen()}
        onChange={(effect) => set({ effect })}
        options={spoken(EFFECTS)}
        value={form.effect}
      />
      <TargetFields errors={errors} form={form} set={set} tools={tools} />
      {fixedChat ? null : (
        <ChatField chats={chats} error={errors.chat} form={form} set={set} />
      )}
      <Advanced
        errors={errors}
        form={form}
        onOpenChange={setAdvancedOpen}
        open={advancedOpen}
        set={set}
      />
      {!invalid && (
        <Preview
          acknowledged={form.acknowledged}
          describe={describe}
          draft={draft}
          editingId={rule?.id}
          onAcknowledge={(acknowledged) =>
            setForm((current) => ({ ...current, acknowledged }))
          }
          rules={rules}
          tools={tools}
        />
      )}
      <div className="text-destructive empty:hidden" role="alert">
        {attempted && invalid
          ? m.rule_fix_the_answers_above_before_saving()
          : attempted && needsAck
            ? m.rule_tick_the_box_above_to_confirm_you_understand()
            : null}
      </div>
      {save.isError && <ErrorState error={save.error} onRetry={send} />}
      <div className="flex flex-wrap gap-2">
        <Button className="h-11 sm:h-9" disabled={save.isPending} type="submit">
          {save.isPending
            ? m.rule_saving()
            : rule
              ? m.rule_save_changes()
              : m.rule_save_rule()}
        </Button>
        {onCancel ? (
          <Button className="h-11 sm:h-9" onClick={onCancel} variant="ghost">
            {m.common_cancel()}
          </Button>
        ) : (
          <Button
            className="h-11 sm:h-9"
            nativeButton={false}
            render={<Link to="/guardrails" />}
            variant="ghost"
          >
            {m.common_cancel()}
          </Button>
        )}
      </div>
    </form>
  );
}
