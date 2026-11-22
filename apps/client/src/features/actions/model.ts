import {
  type Action,
  type ActionStatus,
  type ChatEvent,
  isRecord,
  type Risk,
} from "@/api/types.ts";
import type { Tone } from "@/components/status-text.tsx";
import { toolLabel } from "@/features/catalog/model.ts";
import { between } from "@/lib/format.ts";
import { parseJsonText } from "@/lib/json.ts";
import { m } from "@/paraglide/messages.js";

type Text = () => string;

export const ACTION_STATUS: Record<
  ActionStatus,
  { label: Text; tone: Tone; pulse?: boolean }
> = {
  approved: { label: m.action_approved_starting, tone: "neutral" },
  awaiting_approval: { label: m.action_waiting_for_you, tone: "attention" },
  cancelled: { label: m.action_cancelled, tone: "neutral" },
  denied: { label: m.action_denied_by_policy, tone: "danger" },
  expired: { label: m.action_expired_without_a_decision, tone: "neutral" },
  failed: { label: m.action_failed, tone: "danger" },
  rejected: { label: m.action_rejected, tone: "neutral" },
  running: { label: m.action_running, pulse: true, tone: "live" },
  succeeded: { label: m.action_done, tone: "ok" },
  suspended: { label: m.action_waiting_for_you, tone: "attention" },
  uncertain: { label: m.action_unknown_outcome, tone: "attention" },
};

export const RISK: Record<Risk, { label: Text; hint: Text }> = {
  forbidden: { hint: m.action_always_denied, label: m.action_forbidden },
  read: {
    hint: m.action_looks_at_something_changes_nothing,
    label: m.action_reads,
  },
  sensitive: {
    hint: m.action_touches_credentials_or_widens_access,
    label: m.action_sensitive,
  },
  write: {
    hint: m.action_creates_edits_or_runs_something,
    label: m.action_changes,
  },
};

export const isWaiting = (action: Action): boolean =>
  action.status === "awaiting_approval" || action.status === "suspended";

export const isActive = (action: Action): boolean =>
  action.status === "running" || action.status === "approved";

const args = (action: Action): Record<string, unknown> =>
  isRecord(action.arguments) ? action.arguments : {};

/** The one line a person needs to recognise a tool call, taken from its arguments. */
export function summarize(action: Action): string {
  const a = args(action);
  for (const key of ["url", "command", "path", "name", "job_id", "source_id"]) {
    const value = a[key];
    if (typeof value === "string") {
      return value;
    }
  }
  if (typeof a.code === "string") {
    return a.code.split("\n")[0] ?? "";
  }
  if (Array.isArray(a.actions)) {
    return a.actions
      .map((step) => (isRecord(step) ? (step.url ?? step.action) : undefined))
      .filter((part): part is string => typeof part === "string")
      .join(" → ");
  }
  const fallback = a.action ?? a.op;
  return typeof fallback === "string" ? fallback : "";
}

/** A tool that does several things names which in one argument; the title names that. */
const OPERATIONS: Record<string, { by: string; names: Record<string, Text> }> =
  {
    files: {
      by: "op",
      names: {
        edit: m.action_edit_file,
        export: m.action_export_file,
        import: m.action_import_file,
        list: m.action_list_workspace_files,
        read: m.action_read_file,
        write: m.action_write_file,
      },
    },
    job: {
      by: "action",
      names: { cancel: m.action_cancel_job, wait: m.action_wait_for_job },
    },
    screen: { by: "action", names: { screenshot: m.action_take_screenshot } },
    watch_history: {
      by: "op",
      names: {
        diff: m.action_compare_monitored_versions,
        latest: m.action_view_latest_monitored_version,
        versions: m.action_view_monitoring_history,
      },
    },
  };

const PASSIVE_STEPS = new Set(["extract", "snapshot", "screenshot"]);

/** What one call did, in a few words. */
export function actionTitle(action: Action): string {
  const a = args(action);
  const operation = OPERATIONS[action.tool];
  if (operation) {
    const value = a[operation.by];
    if (typeof value === "string" && value) {
      return (
        operation.names[value]?.() ??
        m.action_text({ tool: toolLabel(action.tool), value })
      );
    }
  }
  if (action.tool === "browser" && Array.isArray(a.actions)) {
    const [first, ...rest] = a.actions;
    if (
      isRecord(first) &&
      first.action === "navigate" &&
      rest.every(
        (step) =>
          isRecord(step) &&
          typeof step.action === "string" &&
          PASSIVE_STEPS.has(step.action),
      )
    ) {
      return m.action_open_webpage();
    }
  }
  if (action.tool === "watch_draft" && a.source_id) {
    return m.action_update_watch_draft();
  }
  return toolLabel(action.tool);
}

const WORKING: Record<string, Text> = {
  browser: m.action_using_the_browser,
  code_run: m.action_running_code,
  job: m.action_waiting_for_a_job,
  screen: m.action_using_the_desktop,
  terminal: m.action_running_a_command,
  watch_draft: m.action_drafting_a_watch,
  watch_history: m.action_reading_watch_history,
  web_fetch: m.action_reading_a_web_page,
};

const FILE_OPS: Record<string, Text> = {
  edit: m.action_editing_a_file,
  export: m.action_exporting_a_file,
  import: m.action_importing_a_file,
  list: m.action_reading_files,
  read: m.action_reading_files,
  write: m.action_writing_a_file,
};

/**
 * What the agent is doing, said from what the backend reports: the status of the turn's
 * tool calls. Nothing here describes the model's thinking.
 */
export function liveLabel(actions: Action[]): { text: string; tone: Tone } {
  if (actions.some(isWaiting)) {
    return { text: m.action_waiting_for_approval(), tone: "attention" };
  }
  const current = actions.filter(isActive).at(-1);
  if (!current) {
    return { text: m.action_working(), tone: "live" };
  }
  const op = args(current).op;
  const text =
    current.tool === "files"
      ? typeof op === "string"
        ? FILE_OPS[op]?.()
        : undefined
      : WORKING[current.tool]?.();
  return { text: text ?? m.action_using_tools(), tone: "live" };
}

const PROBLEMS: readonly [
  ActionStatus,
  (inputs: { n: number }) => string,
  Tone,
][] = [
  ["failed", m.action_failed_2, "danger"],
  ["denied", m.action_blocked_by_a_guardrail, "danger"],
  ["uncertain", m.action_outcome_unknown, "attention"],
  ["expired", m.action_expired_undecided, "neutral"],
  ["rejected", m.action_rejected_2, "neutral"],
  ["cancelled", m.action_cancelled_2, "neutral"],
];

/** The one-line account of a group of tool calls, naming anything that did not go through. */
export function activitySummary(steps: Action[]): {
  text: string;
  problems: { label: string; tone: Tone; count: number }[];
} {
  const end = steps
    .map((step) => step.finished_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  const first = steps[0];
  const calls = m.action_tool_call({ count: steps.length });
  const duration = first && end ? between(first.created_at, end) : "";
  return {
    problems: PROBLEMS.flatMap(([status, text, tone]) => {
      const count = steps.filter((step) => step.status === status).length;
      return count > 0 ? [{ count, label: text({ n: count }), tone }] : [];
    }),
    text: duration ? `${calls} — ${duration}` : calls,
  };
}

/** The feed records the backend wrote for one tool call. */
export const actionRecords = (
  events: ChatEvent[],
  actionId: string,
): ChatEvent[] =>
  events.filter(
    (event) =>
      event.kind === "action" &&
      isRecord(event.data) &&
      event.data.action_id === actionId,
  );

/**
 * The result recorded for a call, or null when none is. The feed stores it as JSON text cut
 * to a preview length, so it is parsed only when it arrived whole.
 */
export function recordedResult(
  records: ChatEvent[],
): { value: unknown } | null {
  const record = records.findLast(
    (event) => isRecord(event.data) && "result" in event.data,
  );
  if (!record || !isRecord(record.data)) {
    return null;
  }
  const value = record.data.result;
  return {
    value: typeof value === "string" ? (parseJsonText(value) ?? value) : value,
  };
}

const delivers = (action: Action): boolean =>
  action.tool === "code_run" ||
  action.tool === "job" ||
  (action.tool === "files" && args(action).op === "export");

/**
 * Files a turn's calls delivered to the user: exported files and the outputs of code runs, as
 * their recorded results name them. Screenshots and results too large to record whole, which
 * the backend stores as a file with a preview, are not deliveries.
 */
export function deliveredArtifactIds(
  actions: Action[],
  events: ChatEvent[],
): string[] {
  return actions.flatMap((action) => {
    if (action.status !== "succeeded" || !delivers(action)) {
      return [];
    }
    const result = recordedResult(actionRecords(events, action.id))?.value;
    if (!isRecord(result) || "preview" in result) {
      return [];
    }
    return [result.artifact_id, result.artifact_ids]
      .flat()
      .filter((id): id is string => typeof id === "string");
  });
}
