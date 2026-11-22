import type { ExecutionStatus } from "@/api/types.ts";
import type { Tone } from "@/components/status-text.tsx";
import { m } from "@/paraglide/messages.js";

type Text = () => string;

export const EXECUTION_STATUS: Record<
  ExecutionStatus,
  { label: Text; tone: Tone; pulse?: boolean }
> = {
  cancelled: { label: m.run_cancelled, tone: "neutral" },
  failed: { label: m.run_failed, tone: "danger" },
  pending: { label: m.run_starting, pulse: true, tone: "live" },
  running: { label: m.run_running, pulse: true, tone: "live" },
  succeeded: { label: m.run_succeeded, tone: "ok" },
  timeout: { label: m.run_timed_out, tone: "danger" },
  uncertain: { label: m.run_outcome_unknown, tone: "attention" },
};

export const EXECUTION_NOTES: Partial<Record<ExecutionStatus, Text>> = {
  timeout: m.run_the_run_exceeded_its_time_limit_and_was,
  uncertain: m.run_the_worker_was_lost_while_this_ran_so,
};

export type Language = "python" | "node" | "bash";

export const LANGUAGES: readonly {
  value: Language;
  label: Text;
  sample: string;
}[] = [
  { label: m.run_python, sample: "print(sum(range(100)))", value: "python" },
  {
    label: m.run_node,
    sample: "console.log([1, 2, 3].map((n) => n * 2))",
    value: "node",
  },
  { label: m.run_bash, sample: "echo hello && uname -a", value: "bash" },
];

/** Export paths as typed: one per line or comma separated, blanks dropped. */
export const parsePaths = (text: string): string[] =>
  text
    .split(/[\n,]/)
    .map((path) => path.trim())
    .filter(Boolean);
