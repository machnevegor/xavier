import type { Effect, Mode, PolicyRuleInput, Risk, Tool } from "@/api/types.ts";
import {
  NO_PLACE,
  SENSITIVE_WHEN,
  toolLabel,
} from "@/features/catalog/model.ts";
import { m } from "@/paraglide/messages.js";

/** What a rule needs for evaluation: a saved rule, or a draft that has no id yet. */
export type RuleLike = Pick<
  PolicyRuleInput,
  "tool_pattern" | "resource_pattern" | "chat_id" | "effect"
> & { priority: number; id?: string };

type EvaluatedRisk = Exclude<Risk, "forbidden">;

/** Defaults by chat mode when no rule matches (src/policy/engine.py). */
export const MODE_DEFAULT: Record<Mode, Record<EvaluatedRisk, Effect>> = {
  autonomous: { read: "allow", sensitive: "ask", write: "allow" },
  read_only: { read: "allow", sensitive: "deny", write: "deny" },
  supervised: { read: "allow", sensitive: "ask", write: "ask" },
};

const TIE_ORDER: Record<Effect, number> = { allow: 2, ask: 1, deny: 0 };

/**
 * Python's `fnmatch.translate`: `*` any run (slashes included), `?` one character, `[...]` a
 * set, `[!...]` its complement; an unclosed `[` is literal. Matching is case-sensitive.
 */
function patternSource(pattern: string): string {
  let source = "";
  for (let i = 0; i < pattern.length; i += 1) {
    const char = pattern.charAt(i);
    if (char === "*") {
      source += "[\\s\\S]*";
    } else if (char === "?") {
      source += "[\\s\\S]";
    } else if (char === "[") {
      let end = i + 1;
      if (pattern[end] === "!") {
        end += 1;
      }
      if (pattern[end] === "]") {
        end += 1;
      }
      while (end < pattern.length && pattern[end] !== "]") {
        end += 1;
      }
      if (end >= pattern.length) {
        source += "\\[";
      } else {
        const body = pattern.slice(i + 1, end).replace(/\\/g, "\\\\");
        source += body.startsWith("!")
          ? `[^${body.slice(1)}]`
          : `[${body.startsWith("^") || body.startsWith("[") ? `\\${body}` : body}]`;
        i = end;
      }
    } else {
      source += char.replace(/[.*+?^${}()|[\]\\/-]/g, "\\$&");
    }
  }
  return source;
}

export function matches(pattern: string, value: string): boolean {
  try {
    return new RegExp(`^${patternSource(pattern)}$`).test(value);
  } catch {
    return false;
  }
}

export const isEverything = (pattern: string | null | undefined): boolean =>
  /^\*+$/.test(pattern ?? "");

/** One value the pattern matches, to try other rules against. */
const sampleOf = (pattern: string): string =>
  pattern
    .replace(/\[(!?)(\]?[^\]]*)\]/g, (_, not: string, body: string) =>
      not ? "x" : (body[0] ?? "x"),
    )
    .replace(/[*?]/g, "x");

/** The engine's `_rank`: compared element by element, the lowest tuple wins. */
function rank(rule: RuleLike): number[] {
  return [
    -rule.priority,
    rule.chat_id ? 0 : 1,
    rule.resource_pattern != null ? 0 : 1,
    -rule.tool_pattern.replaceAll("*", "").length,
    TIE_ORDER[rule.effect],
  ];
}

/** Negative when `a` wins over `b`, positive when `b` wins, zero on a full tie. */
export function precedence(a: RuleLike, b: RuleLike): number {
  const left = rank(a);
  const right = rank(b);
  for (let i = 0; i < left.length; i += 1) {
    const difference = (left[i] ?? 0) - (right[i] ?? 0);
    if (difference !== 0) {
      return difference;
    }
  }
  return 0;
}

export interface Call {
  chatId: string;
  mode: Mode;
  tool: string;
  risk: Risk;
  resources: readonly string[];
}

export function decide(
  rules: readonly RuleLike[],
  call: Call,
): { effect: Effect; rule: RuleLike | null } {
  if (call.risk === "forbidden") {
    return { effect: "deny", rule: null };
  }
  const [rule] = rules
    .filter(
      (r) =>
        (!r.chat_id || r.chat_id === call.chatId) &&
        matches(r.tool_pattern, call.tool) &&
        (r.resource_pattern == null ||
          call.resources.some((value) =>
            matches(r.resource_pattern ?? "", value),
          )),
    )
    .sort(precedence);
  return rule
    ? { effect: rule.effect, rule }
    : { effect: MODE_DEFAULT[call.mode][call.risk], rule: null };
}

const SENTENCE: Record<
  Effect,
  (inputs: { what: string; where: string; scope: string }) => string
> = {
  allow: m.rule_allow_2,
  ask: m.rule_ask_me_before,
  deny: m.rule_block_2,
};

/** A rule as one sentence a person can check against what they meant. */
export function describeRule(rule: RuleLike, chatTitle?: string): string {
  const pattern = rule.tool_pattern;
  const what = isEverything(pattern)
    ? m.rule_any_action()
    : /[*?[]/.test(pattern)
      ? m.rule_actions_matching({ pattern })
      : m.rule_text({ tool: toolLabel(pattern) });
  const where =
    rule.resource_pattern == null
      ? m.rule_anywhere()
      : isEverything(rule.resource_pattern)
        ? m.rule_in_any_place()
        : m.rule_on({ pattern: rule.resource_pattern });
  const scope = rule.chat_id
    ? chatTitle
      ? m.rule_only_in({ chat: chatTitle })
      : m.rule_only_in_one_chat()
    : m.rule_in_all_chats();
  return SENTENCE[rule.effect]({ scope, what, where });
}

export interface PreviewRow {
  tool: Tool;
  risk: Risk;
  when?: string;
  neverApplies: boolean;
  modes: {
    mode: Mode;
    before: Effect;
    after: Effect;
    overruledBy: RuleLike | null;
  }[];
}

/**
 * What a draft would change, for each tool it covers and each chat mode. When a rule is being
 * edited, "before" includes its saved version and "after" replaces it. Tools that are
 * sometimes sensitive get a second row for that case.
 */
export function previewRule(
  draft: RuleLike,
  rules: readonly RuleLike[],
  tools: readonly Tool[],
  editingId?: string,
): PreviewRow[] {
  const others = rules.filter((rule) => rule.id !== editingId);
  const resources =
    draft.resource_pattern != null ? [sampleOf(draft.resource_pattern)] : [];
  const chatId = draft.chat_id ?? "any";
  return tools
    .filter((tool) => matches(draft.tool_pattern, tool.name))
    .flatMap((tool) => {
      const base = tool.risk;
      const when = SENSITIVE_WHEN[tool.name]?.();
      return when && base !== "sensitive" && base !== "forbidden"
        ? [
            { risk: base, tool },
            { risk: "sensitive" as const, tool, when },
          ]
        : [{ risk: base, tool }];
    })
    .map(({ tool, risk, ...rest }) => ({
      ...rest,
      modes: (Object.keys(MODE_DEFAULT) as Mode[]).map((mode) => {
        const call = { chatId, mode, resources, risk, tool: tool.name };
        const after = decide([...others, draft], call);
        return {
          after: after.effect,
          before: decide(rules, call).effect,
          mode,
          overruledBy: after.rule && after.rule !== draft ? after.rule : null,
        };
      }),
      neverApplies: draft.resource_pattern != null && NO_PLACE.has(tool.name),
      risk,
      tool,
    }));
}

function why(winner: RuleLike, loser: RuleLike): string {
  const [w, l] = [rank(winner), rank(loser)];
  if (w[0] !== l[0]) {
    return m.rule_priority_against({
      a: winner.priority,
      b: loser.priority,
    });
  }
  if (w[1] !== l[1] || w[2] !== l[2] || w[3] !== l[3]) {
    return m.rule_it_is_more_specific();
  }
  return m.rule_it_is_stricter_and_they_rank_the_same();
}

/**
 * Other rules with a different outcome that can meet the draft on the same tool, chat and
 * place, split by which side wins there.
 */
export function ruleInteractions<T extends RuleLike>(
  draft: RuleLike,
  rules: readonly T[],
  tools: readonly Tool[],
  editingId?: string,
): {
  yieldsTo: { rule: T; why: string }[];
  overrides: { rule: T; why: string }[];
} {
  const covered = (rule: RuleLike) =>
    tools
      .filter((tool) => matches(rule.tool_pattern, tool.name))
      .map((tool) => tool.name);
  const mine = new Set(covered(draft));
  const meet = (a: string | null | undefined, b: string | null | undefined) =>
    a == null ||
    b == null ||
    matches(a, sampleOf(b)) ||
    matches(b, sampleOf(a));
  const related = rules.filter(
    (rule) =>
      rule.id !== editingId &&
      rule.effect !== draft.effect &&
      (!rule.chat_id || !draft.chat_id || rule.chat_id === draft.chat_id) &&
      covered(rule).some((name) => mine.has(name)) &&
      meet(rule.resource_pattern, draft.resource_pattern),
  );
  return {
    overrides: related
      .filter((rule) => precedence(draft, rule) < 0)
      .map((rule) => ({ rule, why: why(draft, rule) })),
    yieldsTo: related
      .filter((rule) => precedence(draft, rule) >= 0)
      .map((rule) => ({ rule, why: why(rule, draft) })),
  };
}

export interface RuleWarning {
  level: "caution" | "danger";
  text: string;
}

/** Things worth a second look before saving; a `danger` must be acknowledged. */
export function ruleWarnings(
  draft: RuleLike,
  tools: readonly Tool[],
): RuleWarning[] {
  const covered = tools.filter((tool) =>
    matches(draft.tool_pattern, tool.name),
  );
  if (covered.length === 0) {
    return [
      {
        level: "caution",
        text: m.rule_xavier_has_no_action_with_this_name_right(),
      },
    ];
  }
  const everything = covered.length === tools.length;
  const anywhere =
    draft.resource_pattern == null || isEverything(draft.resource_pattern);
  const sensitive = covered.filter(
    (tool) => tool.risk === "sensitive" || SENSITIVE_WHEN[tool.name],
  );
  const warnings: RuleWarning[] = [];
  if (draft.effect === "allow") {
    if (everything && anywhere) {
      warnings.push({
        level: "danger",
        text: m.rule_this_lets_the_agent_do_anything_anywhere_without(),
      });
    } else if (sensitive.length > 0) {
      const cases = sensitive
        .map((tool) => SENSITIVE_WHEN[tool.name]?.() ?? toolLabel(tool.name))
        .join("; ");
      warnings.push({
        level: "danger",
        text: m.rule_this_also_covers_sensitive_cases_without_this_rule({
          cases,
        }),
      });
    } else if (anywhere && covered.some((tool) => tool.risk === "write")) {
      warnings.push({
        level: "caution",
        text: m.rule_this_applies_to_every_website_and_file_limiting(),
      });
    }
    if (!draft.chat_id) {
      warnings.push({
        level: "caution",
        text: m.rule_it_applies_to_every_chat_including_chats_a(),
      });
    }
  }
  if (draft.effect === "deny" && everything && anywhere) {
    warnings.push({
      level: "caution",
      text: m.rule_this_blocks_every_action_so_the_agent_will(),
    });
  }
  if (draft.resource_pattern != null) {
    const placeless = covered.filter((tool) => NO_PLACE.has(tool.name));
    if (placeless.length === 1) {
      const tool = placeless[0];
      if (tool) {
        warnings.push({
          level: "caution",
          text: m.rule_is_not_tied_to_a_website_or_file({
            tool: toolLabel(tool.name),
          }),
        });
      }
    } else if (placeless.length > 1) {
      warnings.push({
        level: "caution",
        text: m.rule_are_not_tied_to_a_website_or_file({
          tools: placeless.map((tool) => toolLabel(tool.name)).join(", "),
        }),
      });
    }
    if (isEverything(draft.resource_pattern)) {
      warnings.push({
        level: "caution",
        text: m.rule_a_lone_matches_every_place_but_only_actions(),
      });
    }
  }
  return warnings;
}

export const sameTarget = (a: RuleLike, b: RuleLike): boolean =>
  a.tool_pattern === b.tool_pattern &&
  (a.resource_pattern ?? null) === (b.resource_pattern ?? null) &&
  (a.chat_id ?? null) === (b.chat_id ?? null);
