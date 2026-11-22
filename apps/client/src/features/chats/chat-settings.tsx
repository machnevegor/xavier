import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { Chat, ToolGroup } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Switch } from "@/components/ui/switch.tsx";
import { catalogQueries } from "@/features/catalog/api.ts";
import { chatKeys, patchChat } from "@/features/chats/api.ts";
import { ColumnBar } from "@/features/chats/chat-header.tsx";
import {
  type ChatOptionsValue,
  ModelItems,
  optionsOf,
  StepLimitField,
  selectedModel,
  stepLimitReady,
  toChatSettings,
} from "@/features/chats/chat-options.tsx";
import { isToolGroup, MODES, TOOL_GROUPS } from "@/features/chats/model.ts";
import { m } from "@/paraglide/messages.js";

function capabilityLabel(name: string): string {
  const group = TOOL_GROUPS.find((item) => item.value === name);
  if (group) {
    return group.label();
  }
  if (name === "execute_network") {
    return m.approval_network_for_code();
  }
  if (name === "proactive") {
    return m.approval_proactive();
  }
  return name;
}

function sameOptions(a: ChatOptionsValue, b: ChatOptionsValue): boolean {
  return (
    a.mode === b.mode &&
    a.model === b.model &&
    a.reasoning === b.reasoning &&
    a.budget_usd === b.budget_usd &&
    a.max_steps === b.max_steps &&
    a.max_steps_custom === b.max_steps_custom &&
    a.execute_network === b.execute_network &&
    a.proactive === b.proactive &&
    a.tool_groups.length === b.tool_groups.length &&
    a.tool_groups.every((group) => b.tool_groups.includes(group))
  );
}

/**
 * Chat settings in the chat column. The title stays under Rename. A save bar sticks to the
 * bottom once something has changed.
 */
export function ChatSettings({
  chat,
  onBack,
}: {
  chat: Chat;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const models = useQuery(catalogQueries.models());
  const [options, setOptions] = useState(() => optionsOf(chat));
  const saved = optionsOf(chat);
  const dirty = !sameOptions(options, saved);
  const patch = useMutation({
    mutationFn: () => patchChat(chat.id, toChatSettings(options)),
    onSuccess: (next) => {
      queryClient.setQueryData(chatKeys.chat(chat.id), next);
      void queryClient.invalidateQueries({ queryKey: chatKeys.all });
      setOptions(optionsOf(next));
    },
  });
  const set = (patchValue: Partial<ChatOptionsValue>) =>
    setOptions((current) => ({ ...current, ...patchValue }));
  const model = selectedModel(models.data, options.model);
  const toggleGroup = (group: ToolGroup, on: boolean) =>
    set({
      tool_groups: on
        ? [...options.tool_groups, group]
        : options.tool_groups.filter((item) => item !== group),
    });

  return (
    <div>
      <ColumnBar
        back={m.chat_back_to_the_conversation()}
        onBack={onBack}
        title={m.chat_settings_2()}
      />
      <form
        className="mx-auto w-full max-w-3xl pt-6 pb-24"
        onSubmit={(event) => {
          event.preventDefault();
          if (dirty && stepLimitReady(options)) {
            patch.mutate();
          }
        }}
      >
        <p className="text-muted-foreground mb-6 text-sm">
          {m.chat_changes_apply_from_the_next_turn_a_running()}
        </p>
        <FieldGroup>
          <FieldSet>
            <FieldLegend>{m.chat_model_3()}</FieldLegend>
            <Field>
              <FieldLabel htmlFor="settings-model">
                {m.chat_model_4()}
              </FieldLabel>
              <Select
                disabled={!models.data}
                onValueChange={(id) =>
                  set({
                    model: typeof id === "string" ? id : null,
                    reasoning: null,
                  })
                }
                value={model?.id ?? null}
              >
                <SelectTrigger className="w-full" id="settings-model">
                  <SelectValue
                    placeholder={
                      models.isError
                        ? m.chat_models_unavailable_2()
                        : m.chat_loading_2()
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  <ModelItems models={models.data ?? []} />
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="settings-reasoning">
                {m.chat_reasoning_2()}
              </FieldLabel>
              <Select
                disabled={!model}
                onValueChange={(level) =>
                  set({ reasoning: typeof level === "string" ? level : null })
                }
                value={options.reasoning ?? model?.reasoning_levels[0] ?? null}
              >
                <SelectTrigger className="w-full" id="settings-reasoning">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(model?.reasoning_levels ?? []).map((level) => (
                    <SelectItem key={level} value={level}>
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </FieldSet>

          <FieldSet>
            <FieldLegend>{m.chat_permissions()}</FieldLegend>
            <Field>
              <FieldLabel htmlFor="settings-mode">{m.chat_mode_2()}</FieldLabel>
              <Select
                onValueChange={(next) => {
                  const mode = MODES.find((option) => option.value === next);
                  if (mode) {
                    set({ mode: mode.value });
                  }
                }}
                value={options.mode}
              >
                <SelectTrigger className="w-full" id="settings-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MODES.map((mode) => (
                    <SelectItem key={mode.value} value={mode.value}>
                      {mode.label()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FieldDescription>
                {MODES.find((mode) => mode.value === options.mode)?.hint()}
              </FieldDescription>
            </Field>
            {TOOL_GROUPS.map((group) => (
              <Field key={group.value} orientation="horizontal">
                <Switch
                  checked={options.tool_groups.includes(group.value)}
                  id={`settings-${group.value}`}
                  onCheckedChange={(on) => toggleGroup(group.value, on)}
                />
                <FieldLabel htmlFor={`settings-${group.value}`}>
                  {group.label()}
                </FieldLabel>
                <FieldDescription>{group.hint()}</FieldDescription>
              </Field>
            ))}
          </FieldSet>

          <FieldSet>
            <FieldLegend>{m.chat_limits()}</FieldLegend>
            <Field>
              <FieldLabel htmlFor="settings-budget">
                {m.chat_budget_usd_2()}
              </FieldLabel>
              <Input
                id="settings-budget"
                inputMode="decimal"
                min="0"
                onChange={(event) => set({ budget_usd: event.target.value })}
                step="0.01"
                type="number"
                value={options.budget_usd}
              />
              <FieldDescription>
                {m.chat_the_chat_pauses_when_this_is_spent()}
              </FieldDescription>
            </Field>
            <StepLimitField
              id="settings-steps"
              onChange={set}
              value={options}
            />
          </FieldSet>

          <Collapsible>
            <CollapsibleTrigger className="text-sm font-medium underline-offset-4 hover:underline">
              {m.chat_advanced_2()}
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-4">
              <FieldSet>
                <Field orientation="horizontal">
                  <Switch
                    checked={options.execute_network}
                    disabled={!options.tool_groups.includes("execute")}
                    id="settings-network"
                    onCheckedChange={(on) => set({ execute_network: on })}
                  />
                  <FieldLabel htmlFor="settings-network">
                    {m.chat_execute_network()}
                  </FieldLabel>
                  <FieldDescription>
                    {m.chat_code_runs_may_reach_the_internet_internal_addresses()}
                  </FieldDescription>
                </Field>
                <Field orientation="horizontal">
                  <Switch
                    checked={options.proactive}
                    id="settings-proactive"
                    onCheckedChange={(on) => set({ proactive: on })}
                  />
                  <FieldLabel htmlFor="settings-proactive">
                    {m.chat_proactive()}
                  </FieldLabel>
                  <FieldDescription>
                    {m.chat_the_agent_may_schedule_wake_ups_and_react_2()}
                  </FieldDescription>
                </Field>
                {chat.declined_capabilities.length > 0 && (
                  <Field>
                    <FieldLabel>{m.chat_declined_capabilities()}</FieldLabel>
                    <p className="text-sm">
                      {chat.declined_capabilities
                        .filter((name) => isToolGroup(name) || name.length > 0)
                        .map(capabilityLabel)
                        .join(", ")}
                    </p>
                    <FieldDescription>
                      {m.chat_the_agent_does_not_ask_for_these_again()}
                    </FieldDescription>
                  </Field>
                )}
              </FieldSet>
            </CollapsibleContent>
          </Collapsible>
        </FieldGroup>
        {patch.isError && (
          <div className="mt-4">
            <ErrorState error={patch.error} />
          </div>
        )}
        {dirty && (
          <div className="bg-background/95 supports-backdrop-filter:bg-background/80 fixed inset-x-0 bottom-(--shell-bottom) z-30 border-t px-4 py-3 backdrop-blur sm:px-8">
            <div className="mx-auto flex max-w-3xl items-center justify-end gap-3">
              <Button
                disabled={
                  patch.isPending ||
                  options.tool_groups.length === 0 ||
                  !stepLimitReady(options)
                }
                type="submit"
              >
                {patch.isPending ? m.chat_saving_2() : m.chat_save_2()}
              </Button>
            </div>
          </div>
        )}
      </form>
    </div>
  );
}
