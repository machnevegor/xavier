import type { Effect, PolicyRule, PolicyRuleInput, Tool } from "@/api/types.ts";
import { m } from "@/paraglide/messages.js";

export const CUSTOM = "custom";

export interface RuleForm {
  effect: Effect;
  tool: string;
  customTool: string;
  where: "anywhere" | "specific";
  resource: string;
  scope: "all" | "chat";
  chatId: string;
  priority: string;
  description: string;
  acknowledged: boolean;
}

export type RuleErrors = Partial<
  Record<"tool" | "resource" | "chat" | "priority", string>
>;

export const EMPTY_RULE: RuleForm = {
  acknowledged: false,
  chatId: "",
  customTool: "",
  description: "",
  effect: "ask",
  priority: "0",
  resource: "",
  scope: "all",
  tool: "",
  where: "anywhere",
};

/** A stored rule as the answers the form asks for. */
export function fromRule(rule: PolicyRule, tools: readonly Tool[]): RuleForm {
  const listed =
    rule.tool_pattern === "*" ||
    tools.some((tool) => tool.name === rule.tool_pattern);
  return {
    ...EMPTY_RULE,
    chatId: rule.chat_id ?? "",
    customTool: listed ? "" : rule.tool_pattern,
    description: rule.description,
    effect: rule.effect,
    priority: String(rule.priority),
    resource: rule.resource_pattern ?? "",
    scope: rule.chat_id ? "chat" : "all",
    tool: listed ? rule.tool_pattern : CUSTOM,
    where: rule.resource_pattern != null ? "specific" : "anywhere",
  };
}

/** The form in the shape the API takes. */
export function toDraft(
  form: RuleForm,
): PolicyRuleInput & { priority: number } {
  return {
    chat_id: form.scope === "chat" ? form.chatId || null : null,
    description: form.description.trim(),
    effect: form.effect,
    priority: Number(form.priority),
    resource_pattern:
      form.where === "specific" ? form.resource.trim() || null : null,
    tool_pattern: (form.tool === CUSTOM ? form.customTool : form.tool).trim(),
  };
}

/**
 * Only what the API would refuse or what leaves an answer unfinished. Anything the API
 * accepts, however broad, is a warning in the preview and never an error here.
 */
export function validateRule(form: RuleForm): RuleErrors {
  const draft = toDraft(form);
  const errors: RuleErrors = {};
  if (!draft.tool_pattern) {
    errors.tool =
      form.tool === CUSTOM
        ? m.rule_type_an_action_name_or_a_pattern_for()
        : m.rule_choose_which_action_this_rule_is_about();
  }
  if (form.where === "specific" && !draft.resource_pattern) {
    errors.resource = m.rule_type_a_website_address_or_file_path_or();
  }
  if (form.scope === "chat" && !draft.chat_id) {
    errors.chat = m.rule_choose_the_chat_this_rule_is_for();
  }
  if (
    !/^-?\d+$/.test(form.priority.trim()) ||
    !Number.isSafeInteger(draft.priority)
  ) {
    errors.priority = m.rule_use_a_whole_number_such_as_0_10();
  }
  return errors;
}

/** The control to move to for the first unfinished answer, top to bottom. */
export function firstProblem(
  errors: RuleErrors,
  form: RuleForm,
): string | null {
  if (errors.tool) {
    return form.tool === CUSTOM ? "rule-tool-pattern" : "rule-tool";
  }
  if (errors.resource) {
    return "rule-resource";
  }
  if (errors.chat) {
    return "rule-chat";
  }
  if (errors.priority) {
    return "rule-priority";
  }
  return null;
}
