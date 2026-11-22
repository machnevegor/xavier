import { isRecord } from "@/api/types.ts";

/** A non-empty string the agent set on the tool call, if any. */
export function agentText(args: unknown, key: string): string | undefined {
  if (!isRecord(args)) {
    return undefined;
  }
  const value = args[key];
  return typeof value === "string" && value !== "" ? value : undefined;
}

/** Operator typed value wins; otherwise the agent's suggestion from the placeholder. */
export function resolveWithAgent(
  typed: string,
  agent: string | undefined,
): string {
  const chosen = typed.trim();
  if (chosen) {
    return chosen;
  }
  return agent ?? "";
}
