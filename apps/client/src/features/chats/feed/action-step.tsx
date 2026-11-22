import { useMemo } from "react";
import { type Action, type ChatEvent, isRecord } from "@/api/types.ts";
import { CodeBlock } from "@/components/code-block.tsx";
import { ValueView } from "@/components/data-view.tsx";
import { Disclosure } from "@/components/disclosure.tsx";
import { FeedRecord } from "@/components/feed-surface.tsx";
import { Status } from "@/components/status-text.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import {
  ACTION_STATUS,
  actionRecords,
  actionTitle,
  RISK,
  recordedResult,
  summarize,
} from "@/features/actions/model.ts";
import { ArtifactChips } from "@/features/artifacts/artifact-list.tsx";
import { useToolIndex } from "@/features/catalog/api.ts";
import { between, hourMinute } from "@/lib/format.ts";
import { toCode } from "@/lib/json.ts";
import { asSchema } from "@/lib/schema.ts";
import { m } from "@/paraglide/messages.js";

const NOTES: Partial<Record<Action["status"], () => string>> = {
  denied: m.chat_a_guardrail_stopped_this_call_before_it_reached,
  expired: m.chat_nobody_decided_in_time_so_it_was_not,
  uncertain: m.chat_it_was_interrupted_so_it_may_or_may,
};

const DECISION: Record<NonNullable<Action["decision"]>, () => string> = {
  approve: m.chat_you_approved,
  cancel: m.chat_cancelled,
  reject: m.chat_you_rejected,
};

function permittedBy(action: Action): string {
  const byRule = Boolean(action.rule_id);
  if (action.effect === "allow") {
    return byRule
      ? m.chat_allowed_by_a_rule()
      : m.chat_allowed_by_the_chats_mode();
  }
  if (action.effect === "ask") {
    return byRule
      ? m.chat_asked_you_by_a_rule()
      : m.chat_asked_you_by_the_chats_mode();
  }
  return byRule ? m.chat_denied_by_a_rule() : m.chat_denied_by_the_chats_mode();
}

const ARTIFACT_KEYS = ["artifact_id", "output_artifact_ids", "artifact_ids"];

/** A result's own id and status repeat what the call's line already says. */
const isEmptyResult = (result: unknown): boolean =>
  result === null ||
  result === undefined ||
  result === "" ||
  (Array.isArray(result) && result.length === 0) ||
  (isRecord(result) &&
    Object.keys(result).every(
      (key) => key === "action_id" || key === "status",
    ));

/** A value worth a field. False truncation flags say nothing, so they stay out. */
function kept(key: string, value: unknown): boolean {
  if (value === undefined || value === null || value === "") {
    return false;
  }
  if (key === "truncated" || key === "elements_truncated") {
    return value === true;
  }
  return true;
}

/** Steps that already succeeded repeat the call's own success, so only failures stay. */
function failedSteps(value: unknown): unknown[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  return value.filter((item) => !(isRecord(item) && item.ok === true));
}

/**
 * Fields of a recorded object that still need a label. Ids, status, and successful steps are
 * removed; a named key in `skip` was already shown somewhere else.
 */
function displayRecord(
  result: Record<string, unknown>,
  skip: readonly string[] = [],
): Record<string, unknown> {
  const hidden = new Set<string>(["action_id", "status", ...skip]);
  const fields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(result)) {
    if (hidden.has(key) || !kept(key, value)) {
      continue;
    }
    if (key === "results") {
      const failed = failedSteps(value);
      if (failed === null) {
        fields.results = value;
      } else if (failed.length > 0) {
        fields.results = failed;
      }
      continue;
    }
    fields[key] = value;
  }
  return fields;
}

function takeKeys(
  result: Record<string, unknown>,
  keys: readonly string[],
): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  for (const key of keys) {
    if (kept(key, result[key])) {
      fields[key] = result[key];
    }
  }
  return fields;
}

function argument(action: Action, key: string): unknown {
  return isRecord(action.arguments) ? action.arguments[key] : undefined;
}

interface Shown {
  exit: number | null;
  stdout: string | null;
  stderr: string | null;
  preview: string | null;
  files: string[];
  fields: Record<string, unknown>;
  /** A result that is not an object, shown as itself. */
  plain: unknown;
}

function blank(partial: Partial<Shown> = {}): Shown {
  return {
    exit: null,
    stdout: null,
    stderr: null,
    preview: null,
    files: [],
    fields: {},
    plain: undefined,
    ...partial,
  };
}

function hasShown(shown: Shown): boolean {
  return (
    shown.plain !== undefined ||
    shown.exit !== null ||
    shown.stdout !== null ||
    shown.stderr !== null ||
    shown.preview !== null ||
    shown.files.length > 0 ||
    Object.keys(shown.fields).length > 0
  );
}

function takeString(
  result: Record<string, unknown>,
  key: string,
): string | null {
  const value = result[key];
  if (typeof value !== "string") {
    return null;
  }
  delete result[key];
  return value || null;
}

function artifactIds(result: Record<string, unknown>): string[] {
  return ARTIFACT_KEYS.flatMap((key) =>
    [result[key]]
      .flat()
      .filter((id): id is string => typeof id === "string" && id !== ""),
  );
}

/** Program output keeps its exit code, streams, and files; everything else is fields. */
function programShown(
  result: Record<string, unknown>,
  skip: readonly string[],
): Shown {
  const rest: Record<string, unknown> = { ...result };
  const stdout = takeString(rest, "stdout");
  const stderr = takeString(rest, "stderr");
  const preview = takeString(rest, "preview");
  const exit = typeof rest.exit_code === "number" ? rest.exit_code : null;
  delete rest.exit_code;
  const files = artifactIds(rest);
  for (const key of ARTIFACT_KEYS) {
    delete rest[key];
  }
  return blank({
    exit,
    fields: displayRecord(rest, skip),
    files,
    preview,
    stderr,
    stdout,
  });
}

interface Split {
  outside: Shown;
  inside: Record<string, unknown> | null;
}

function finish(outside: Shown, inside: Record<string, unknown>): Split {
  return {
    inside: Object.keys(inside).length > 0 ? inside : null,
    outside,
  };
}

/** Page address, title, and text stay outside; the element list and failed steps do not. */
function splitBrowser(result: Record<string, unknown>): Split {
  const snapshot = isRecord(result.snapshot) ? result.snapshot : {};
  const inside: Record<string, unknown> = {};
  for (const key of ["elements", "scroll", "tabs", "elements_truncated"]) {
    if (kept(key, snapshot[key])) {
      inside[key] = snapshot[key];
    }
  }
  Object.assign(
    inside,
    displayRecord(snapshot, [
      "url",
      "title",
      "text",
      "elements",
      "scroll",
      "tabs",
      "elements_truncated",
    ]),
    displayRecord(result, ["snapshot"]),
  );
  return finish(
    blank({ fields: takeKeys(snapshot, ["url", "title", "text"]) }),
    inside,
  );
}

function splitNamed(
  result: Record<string, unknown>,
  keys: readonly string[],
): Split {
  return finish(
    blank({ fields: takeKeys(result, keys) }),
    displayRecord(result, keys),
  );
}

function splitFiles(action: Action, result: Record<string, unknown>): Split {
  const op = argument(action, "op");
  if (op === "read") {
    return splitNamed(result, ["path", "size", "text", "binary"]);
  }
  if (op === "list") {
    return splitNamed(result, ["entries"]);
  }
  if (op === "export" || op === "import") {
    const draft: Record<string, unknown> = { ...result };
    const files = artifactIds(draft);
    const fromArgs = argument(action, "artifact_id");
    if (
      op === "import" &&
      typeof fromArgs === "string" &&
      fromArgs !== "" &&
      !files.includes(fromArgs)
    ) {
      files.push(fromArgs);
    }
    for (const key of ARTIFACT_KEYS) {
      delete draft[key];
    }
    return finish(blank({ files }), displayRecord(draft, ["path"]));
  }
  return finish(programShown(result, []), {});
}

/** A screenshot is the picture. Its size and marks stay with the other details. */
function splitScreenshot(result: Record<string, unknown>): Split {
  const draft: Record<string, unknown> = { ...result };
  const files = artifactIds(draft);
  for (const key of ARTIFACT_KEYS) {
    delete draft[key];
  }
  return finish(blank({ files }), displayRecord(draft));
}

/**
 * What a person can read without opening the call, and the fields that stay inside it.
 * Nothing is invented when the recorded object lacks it.
 */
function splitResult(action: Action, result: unknown): Split {
  if (!isRecord(result)) {
    return { inside: null, outside: blank({ plain: result }) };
  }
  if (action.tool === "browser") {
    return splitBrowser(result);
  }
  if (action.tool === "web_fetch") {
    return splitNamed(result, ["title", "url", "http_status", "text"]);
  }
  if (action.tool === "files") {
    return splitFiles(action, result);
  }
  if (action.tool === "screen" && argument(action, "action") === "screenshot") {
    return splitScreenshot(result);
  }
  const skip =
    action.tool === "code_run" ||
    action.tool === "terminal" ||
    action.tool === "job"
      ? ["job_id", "job_status"]
      : [];
  return finish(programShown(result, skip), {});
}

/**
 * A result in the documented shapes: program output with an exit code, stored files, or a
 * preview of a result too large to return whole. Anything else is laid out by its shape.
 */
function ResultView({ shown }: { shown: Shown }) {
  if (!hasShown(shown)) {
    return null;
  }
  const preview = shown.preview ? toCode(shown.preview) : null;
  return (
    <FeedRecord label={m.chat_result()}>
      {shown.plain !== undefined ? (
        <ValueView value={shown.plain} />
      ) : (
        <div className="space-y-2">
          {shown.exit !== null && (
            <p className="text-sm">
              {m.chat_exit_code()}{" "}
              <span className="font-mono text-xs">{shown.exit}</span>
            </p>
          )}
          {shown.stdout && (
            <CodeBlock bare code={shown.stdout} label={m.chat_output()} />
          )}
          {shown.stderr && (
            <CodeBlock
              bare
              className={
                shown.exit
                  ? "border-destructive rounded-l-none border-l-2"
                  : undefined
              }
              code={shown.stderr}
              label={m.chat_errors()}
            />
          )}
          {preview && shown.preview && (
            <CodeBlock
              bare
              code={preview.code}
              copy={shown.preview}
              label={m.chat_preview_of_a_larger_result()}
              language={preview.language}
            />
          )}
          {shown.files.length > 0 && <ArtifactChips ids={shown.files} />}
          {Object.keys(shown.fields).length > 0 && (
            <ValueView value={shown.fields} />
          )}
        </div>
      )}
    </FeedRecord>
  );
}

/**
 * The quiet last line of an opened call: its technical name, kind, how it was permitted and
 * when it started, with the untouched record one click away.
 */
function ActionFooter({
  action,
  records,
}: {
  action: Action;
  records: ChatEvent[];
}) {
  const done = action.status === "succeeded";
  const started = hourMinute(action.created_at);
  const decision = action.decision ? DECISION[action.decision]() : "";
  return (
    <Collapsible>
      <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 text-xs">
        {done && <span>{m.chat_done()}</span>}
        <span className="font-mono">{action.tool}</span>
        <span>{RISK[action.risk].label()}</span>
        <span>
          {permittedBy(action)}
          {decision}
        </span>
        <span>
          {action.finished_at
            ? m.chat_started_took({
                duration: between(action.created_at, action.finished_at),
                time: started,
              })
            : m.chat_started({ time: started })}
        </span>
        <CollapsibleTrigger className="hover:text-foreground min-h-11 rounded underline underline-offset-2 sm:min-h-8">
          {m.chat_view_raw_json()}
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent className="mt-2">
        <CodeBlock
          code={JSON.stringify({ action, events: records }, null, 2)}
          language="json"
        />
      </CollapsibleContent>
    </Collapsible>
  );
}

function ActionDetails({
  action,
  records,
  leftover,
}: {
  action: Action;
  records: ChatEvent[];
  leftover: Record<string, unknown> | null;
}) {
  const tool = useToolIndex().get(action.tool);
  const note = NOTES[action.status]?.();
  return (
    <>
      {note && <p className="text-attention">{note}</p>}
      <FeedRecord label={m.chat_arguments()}>
        <ValueView
          schema={asSchema(tool?.input_schema)}
          value={action.arguments}
        />
      </FeedRecord>
      {leftover ? (
        <FeedRecord>
          <ValueView value={leftover} />
        </FeedRecord>
      ) : null}
      <ActionFooter action={action} records={records} />
    </>
  );
}

/** One tool call: what it was and how it ended. Its record opens below it. */
export function ActionStep({
  action,
  events,
}: {
  action: Action;
  events: ChatEvent[];
}) {
  const target = summarize(action);
  const records = useMemo(
    () => actionRecords(events, action.id),
    [events, action.id],
  );
  const record = recordedResult(records);
  const parts =
    record && !isEmptyResult(record.value)
      ? splitResult(action, record.value)
      : null;
  const outside = parts && hasShown(parts.outside) ? parts.outside : null;
  const history =
    !record && action.status === "succeeded"
      ? m.chat_this_result_isnt_in_the_loaded_history()
      : null;
  const missingError =
    action.status === "failed" && !action.error && !outside
      ? m.chat_no_error_details_were_recorded()
      : null;
  const body =
    Boolean(action.error) ||
    Boolean(history) ||
    Boolean(missingError) ||
    Boolean(outside);
  return (
    <li className="relative py-2 pl-3">
      <span
        aria-hidden="true"
        className="bg-border absolute top-[1.35rem] left-0 size-1.5 -translate-x-1/2 rounded-full"
      />
      <div className="flex min-h-9 w-full items-center gap-2">
        <span className="min-w-0 flex-1 truncate">
          {actionTitle(action)}
          {target && (
            <span className="text-muted-foreground ml-2 font-mono text-xs">
              {target.slice(0, 160)}
            </span>
          )}
        </span>
        {action.status === "succeeded" ? (
          <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
            {between(action.created_at, action.finished_at)}
          </span>
        ) : (
          <Status
            meta={{
              label: ACTION_STATUS[action.status].label(),
              pulse: ACTION_STATUS[action.status].pulse,
              tone: ACTION_STATUS[action.status].tone,
            }}
          />
        )}
      </div>
      {body && (
        <div className="mt-1.5 space-y-2">
          {action.error && (
            <p className="border-destructive border-l-2 py-0.5 pl-3">
              {action.error_code && (
                <span className="text-muted-foreground mr-2 font-mono text-xs">
                  {action.error_code}
                </span>
              )}
              {action.error}
            </p>
          )}
          {history && <p className="text-muted-foreground">{history}</p>}
          {missingError && (
            <p className="text-muted-foreground">{missingError}</p>
          )}
          {outside && <ResultView shown={outside} />}
        </div>
      )}
      <Disclosure
        className="mt-0.5"
        contentClassName="space-y-3 pb-1 pt-1"
        label={m.chat_technical_details()}
      >
        <ActionDetails
          action={action}
          leftover={parts?.inside ?? null}
          records={records}
        />
      </Disclosure>
    </li>
  );
}
