import type { Effect } from "@/api/types.ts";
import type { Tone } from "@/components/status-text.tsx";
import { m } from "@/paraglide/messages.js";

type Text = () => string;

export const EFFECT: Record<Effect, { label: Text; tone: Tone; hint: Text }> = {
  allow: {
    hint: m.rule_the_agent_goes_ahead_without_asking,
    label: m.rule_allow,
    tone: "ok",
  },
  ask: {
    hint: m.rule_the_agent_waits_for_your_decision_each_time,
    label: m.rule_ask_me,
    tone: "attention",
  },
  deny: {
    hint: m.rule_the_agent_is_refused_whatever_it_is_told,
    label: m.rule_block,
    tone: "danger",
  },
};
