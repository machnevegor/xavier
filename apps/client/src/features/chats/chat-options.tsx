import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import {
  type Chat,
  isRecord,
  type Mode,
  type Model,
  type ToolGroup,
} from "@/api/types.ts";
import { Disclosure } from "@/components/disclosure.tsx";
import { Checkbox } from "@/components/ui/checkbox.tsx";
import {
  Field,
  FieldDescription,
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group.tsx";
import { catalogQueries } from "@/features/catalog/api.ts";
import { isToolGroup, MODES, TOOL_GROUPS } from "@/features/chats/model.ts";
import { m } from "@/paraglide/messages.js";

export const STEP_PRESETS = ["10", "25", "50", "100", "250"] as const;

export type StepChoice = (typeof STEP_PRESETS)[number] | "custom" | "none";

function isStepChoice(value: string): value is StepChoice {
  return (
    value === "custom" ||
    value === "none" ||
    STEP_PRESETS.some((preset) => preset === value)
  );
}

export interface ChatOptionsValue {
  tool_groups: ToolGroup[];
  execute_network: boolean;
  proactive: boolean;
  mode: Mode;
  model: string | null;
  reasoning: string | null;
  budget_usd: string;
  max_steps: StepChoice;
  max_steps_custom: string;
}

/** What a first visit starts with; anything left null is the backend's default. */
export const DEFAULT_OPTIONS: ChatOptionsValue = {
  budget_usd: "",
  execute_network: true,
  max_steps: "50",
  max_steps_custom: "",
  mode: "supervised",
  model: null,
  proactive: false,
  reasoning: null,
  tool_groups: ["observe", "execute"],
};

const LAST_OPTIONS_KEY = "xavier.new-chat-options";

/** The options of the last new chat, field by field, falling back to the first-visit ones. */
function readLastOptions(): ChatOptionsValue {
  let saved: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(
      window.localStorage.getItem(LAST_OPTIONS_KEY) ?? "{}",
    );
    saved = isRecord(parsed) ? parsed : {};
  } catch {
    // Unreadable storage starts over from the first-visit options.
  }
  const text = (value: unknown) => (typeof value === "string" ? value : null);
  const budget = text(saved.budget_usd);
  const steps = text(saved.max_steps);
  const custom = text(saved.max_steps_custom);
  const customSteps = Number(custom);
  const rememberedCustom =
    custom !== null && Number.isInteger(customSteps) && customSteps >= 1
      ? custom
      : DEFAULT_OPTIONS.max_steps_custom;
  const groups: unknown[] | null = Array.isArray(saved.tool_groups)
    ? saved.tool_groups
    : null;
  return {
    budget_usd:
      budget !== null && Number.isFinite(Number(budget))
        ? budget
        : DEFAULT_OPTIONS.budget_usd,
    execute_network:
      typeof saved.execute_network === "boolean"
        ? saved.execute_network
        : DEFAULT_OPTIONS.execute_network,
    max_steps:
      steps !== null && isStepChoice(steps) ? steps : DEFAULT_OPTIONS.max_steps,
    max_steps_custom: rememberedCustom,
    mode:
      MODES.find((mode) => mode.value === saved.mode)?.value ??
      DEFAULT_OPTIONS.mode,
    model: text(saved.model),
    proactive:
      typeof saved.proactive === "boolean"
        ? saved.proactive
        : DEFAULT_OPTIONS.proactive,
    reasoning: text(saved.reasoning),
    tool_groups: groups
      ? TOOL_GROUPS.map((group) => group.value).filter((group) =>
          groups.includes(group),
        )
      : DEFAULT_OPTIONS.tool_groups,
  };
}

/**
 * The options of a new chat: those of the last one, so the same setup is not chosen again
 * each time. They are remembered in this browser as they change.
 */
export function useLastOptions(): [
  ChatOptionsValue,
  (value: ChatOptionsValue) => void,
] {
  const [options, setOptions] = useState(readLastOptions);
  const update = (value: ChatOptionsValue) => {
    setOptions(value);
    try {
      window.localStorage.setItem(LAST_OPTIONS_KEY, JSON.stringify(value));
    } catch {
      // Storage can be disabled; the options then last for this page only.
    }
  };
  return [options, update];
}

/**
 * Drops a remembered model or reasoning level the backend no longer offers, so the backend's
 * defaults apply instead of a request it would refuse.
 */
export function offeredOptions(
  options: ChatOptionsValue,
  models: Model[] | undefined,
): ChatOptionsValue {
  if (!models) {
    return options;
  }
  const model = models.find((item) =>
    options.model === null ? item.default : item.id === options.model,
  );
  return {
    ...options,
    model: model ? options.model : null,
    reasoning:
      model &&
      options.reasoning &&
      model.reasoning_levels.includes(options.reasoning)
        ? options.reasoning
        : null,
  };
}

function choiceOf(
  maxSteps: number | null,
): Pick<ChatOptionsValue, "max_steps" | "max_steps_custom"> {
  if (maxSteps === null) {
    return { max_steps: "none", max_steps_custom: "" };
  }
  const text = String(maxSteps);
  return isStepChoice(text) && text !== "custom" && text !== "none"
    ? { max_steps: text, max_steps_custom: "" }
    : { max_steps: "custom", max_steps_custom: text };
}

/** A custom step count is a whole number of at least 1. Presets and no limit are ready. */
export function stepLimitReady(options: ChatOptionsValue): boolean {
  if (options.max_steps !== "custom") {
    return true;
  }
  const value = Number(options.max_steps_custom);
  return Number.isInteger(value) && value >= 1;
}

function stepLimitValue(options: ChatOptionsValue): number | null {
  if (options.max_steps === "none") {
    return null;
  }
  if (options.max_steps === "custom") {
    return Number(options.max_steps_custom);
  }
  return Number(options.max_steps);
}

export const optionsOf = (chat: Chat): ChatOptionsValue => ({
  budget_usd: String(chat.budget_usd),
  execute_network: chat.execute_network,
  ...choiceOf(chat.max_steps),
  mode: chat.mode,
  model: chat.model,
  proactive: chat.proactive,
  reasoning: chat.reasoning,
  tool_groups: chat.tool_groups.filter(
    (group): group is ToolGroup =>
      isToolGroup(group) &&
      group !== "files" &&
      group !== "always" &&
      group !== "proactive",
  ),
});

/** The request fields, leaving anything unset to the backend's defaults. */
export function toChatSettings(options: ChatOptionsValue): {
  tool_groups: ToolGroup[];
  execute_network: boolean;
  proactive: boolean;
  mode: Mode;
  model: string | null;
  reasoning: string | null;
  budget_usd: number | null;
  max_steps: number | null;
} {
  return {
    budget_usd: options.budget_usd === "" ? null : Number(options.budget_usd),
    execute_network:
      options.tool_groups.includes("execute") && options.execute_network,
    mode: options.mode,
    model: options.model,
    proactive: options.proactive,
    max_steps: stepLimitValue(options),
    reasoning: options.reasoning,
    tool_groups: options.tool_groups,
  };
}

export function selectedModel(
  models: Model[] | undefined,
  id: string | null,
): Model | undefined {
  return (
    models?.find((model) => model.id === id) ??
    models?.find((model) => model.default)
  );
}

/** The models to choose from, each noting only what it cannot do: images and reasoning are expected. */
export function ModelItems({ models }: { models: Model[] }) {
  return models.map((model) => {
    const limits = [
      !model.input.includes("image") ? m.chat_no_images() : null,
      model.reasoning_levels.every((level) => level === "off")
        ? m.chat_no_reasoning()
        : null,
    ].filter((limit) => limit !== null);
    return (
      <SelectItem key={model.id} value={model.id}>
        {model.id}
        {limits.length > 0 && (
          <span className="text-muted-foreground">— {limits.join(", ")}</span>
        )}
      </SelectItem>
    );
  });
}

/** Supervised, autonomous, or read only, with the chosen mode's one-line explanation. */
export function ModeToggle({
  describedBy,
  value,
  onChange,
}: {
  describedBy: string;
  value: Mode;
  onChange: (mode: Mode) => void;
}) {
  const mode = MODES.find((option) => option.value === value);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <ToggleGroup
        aria-label={m.chat_how_much_it_asks_first()}
        onValueChange={([next]) => {
          const picked = MODES.find((option) => option.value === next);
          if (picked) {
            onChange(picked.value);
          }
        }}
        size="sm"
        spacing={0}
        value={[value]}
        variant="outline"
      >
        {MODES.map((option) => (
          <ToggleGroupItem
            aria-describedby={describedBy}
            className="text-muted-foreground aria-pressed:text-foreground"
            key={option.value}
            value={option.value}
          >
            {option.label()}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <span className="text-muted-foreground text-xs" id={describedBy}>
        {mode?.hint()}
      </span>
    </div>
  );
}

/** Secondary settings of a chat, shared by a new chat and a scheduled one. */
export function ChatOptions({
  value,
  onChange,
}: {
  value: ChatOptionsValue;
  onChange: (value: ChatOptionsValue) => void;
}) {
  const id = useId();
  const models = useQuery(catalogQueries.models());
  const set = (patch: Partial<ChatOptionsValue>) =>
    onChange({ ...value, ...patch });
  const model = selectedModel(models.data, value.model);
  const toggleGroup = (group: ToolGroup, on: boolean) =>
    set({
      tool_groups: on
        ? [...value.tool_groups, group]
        : value.tool_groups.filter((item) => item !== group),
    });

  const execute = value.tool_groups.includes("execute");
  return (
    <div className="space-y-6">
      <FieldSet className="gap-2">
        <FieldLegend variant="label">
          {m.chat_what_the_agent_may_use()}
        </FieldLegend>
        {TOOL_GROUPS.map((group) => (
          <label
            className="flex cursor-pointer items-start gap-3 py-1"
            htmlFor={`${id}-${group.value}`}
            key={group.value}
          >
            <Checkbox
              checked={value.tool_groups.includes(group.value)}
              className="mt-0.5"
              id={`${id}-${group.value}`}
              onCheckedChange={(on) => toggleGroup(group.value, on)}
            />
            <span>
              <span className="font-medium">{group.label()}</span>
              <span className="text-muted-foreground block">
                {group.hint()}
              </span>
            </span>
          </label>
        ))}
      </FieldSet>

      <div className="grid gap-6 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={`${id}-model`}>{m.chat_model()}</FieldLabel>
          <Select
            disabled={!models.data}
            onValueChange={(next) =>
              set({
                model: typeof next === "string" ? next : null,
                reasoning: null,
              })
            }
            value={model?.id ?? null}
          >
            <SelectTrigger className="w-full" id={`${id}-model`}>
              <SelectValue
                placeholder={
                  models.isError
                    ? m.chat_models_unavailable()
                    : m.chat_loading()
                }
              />
            </SelectTrigger>
            <SelectContent>
              <ModelItems models={models.data ?? []} />
            </SelectContent>
          </Select>
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-reasoning`}>
            {m.chat_reasoning()}
          </FieldLabel>
          <Select
            disabled={!model}
            onValueChange={(level) =>
              set({ reasoning: typeof level === "string" ? level : null })
            }
            value={value.reasoning ?? model?.reasoning_levels[0] ?? null}
          >
            <SelectTrigger className="w-full" id={`${id}-reasoning`}>
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
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={`${id}-budget`}>
            {m.chat_budget_usd()}
          </FieldLabel>
          <Input
            id={`${id}-budget`}
            inputMode="decimal"
            min="0"
            onChange={(event) => set({ budget_usd: event.target.value })}
            step="0.01"
            type="number"
            value={value.budget_usd}
          />
          <FieldDescription>
            {m.chat_the_chat_pauses_when_this_is_spent_leave()}
          </FieldDescription>
        </Field>
        <div className="space-y-6">
          <StepLimitField id={`${id}-steps`} onChange={set} value={value} />
        </div>
      </div>

      <Disclosure contentClassName="mt-3 space-y-4" label={m.chat_advanced()}>
        {execute && (
          <label
            className="flex cursor-pointer items-center gap-3"
            htmlFor={`${id}-network`}
          >
            <Switch
              checked={value.execute_network}
              id={`${id}-network`}
              onCheckedChange={(on) => set({ execute_network: on })}
            />
            {m.chat_let_code_runs_access_the_internet()}
          </label>
        )}
        <label
          className="flex cursor-pointer items-start gap-3"
          htmlFor={`${id}-proactive`}
        >
          <Switch
            checked={value.proactive}
            className="mt-0.5"
            id={`${id}-proactive`}
            onCheckedChange={(on) => set({ proactive: on })}
          />
          <span>
            <span className="font-medium">{m.chat_proactive_mode()}</span>
            <span className="text-muted-foreground block">
              {m.chat_the_agent_may_schedule_wake_ups_and_react()}
            </span>
          </span>
        </label>
      </Disclosure>
    </div>
  );
}

/** How many steps one turn may take: a preset, a custom number, or no limit. */
export function StepLimitField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: ChatOptionsValue;
  onChange: (patch: Partial<ChatOptionsValue>) => void;
}) {
  const custom = value.max_steps === "custom";
  return (
    <>
      <Field>
        <FieldLabel htmlFor={id}>{m.chat_step_limit()}</FieldLabel>
        <Select
          onValueChange={(next) => {
            if (typeof next === "string" && isStepChoice(next)) {
              onChange({ max_steps: next });
            }
          }}
          value={value.max_steps}
        >
          <SelectTrigger className="w-full" id={id}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STEP_PRESETS.map((preset) => (
              <SelectItem key={preset} value={preset}>
                {preset}
              </SelectItem>
            ))}
            <SelectItem value="custom">{m.chat_custom()}</SelectItem>
            <SelectItem value="none">{m.chat_no_limit()}</SelectItem>
          </SelectContent>
        </Select>
        <FieldDescription>
          {m.chat_one_turn_stops_after_this_many_steps_no()}
        </FieldDescription>
      </Field>
      {custom && (
        <Field>
          <FieldLabel htmlFor={`${id}-custom`}>
            {m.chat_custom_steps()}
          </FieldLabel>
          <Input
            id={`${id}-custom`}
            inputMode="numeric"
            min={1}
            onChange={(event) =>
              onChange({ max_steps_custom: event.target.value })
            }
            step={1}
            type="number"
            value={value.max_steps_custom}
          />
          {!stepLimitReady(value) && (
            <FieldDescription>
              {m.chat_enter_a_whole_number_of_at_least_1()}
            </FieldDescription>
          )}
        </Field>
      )}
    </>
  );
}
