import { IconShieldQuestion } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/errors.ts";
import { type Action, isRecord, type Tool } from "@/api/types.ts";
import { CodeBlock, JsonBlock } from "@/components/code-block.tsx";
import { RawJson, ValueView } from "@/components/data-view.tsx";
import { Disclosure } from "@/components/disclosure.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { actionTitle, summarize } from "@/features/actions/model.ts";
import { RiskLabel } from "@/features/actions/risk-label.tsx";
import {
  AlwaysAllowPanel,
  resourcesOf,
} from "@/features/approvals/always-allow-panel.tsx";
import { DecisionCard } from "@/features/approvals/decision-card.tsx";
import {
  isGone,
  useApprovalDecision,
} from "@/features/approvals/use-approval-decision.ts";
import { catalogQueries } from "@/features/catalog/api.ts";
import { timeAgo } from "@/lib/format.ts";
import { asSchema } from "@/lib/schema.ts";
import { m } from "@/paraglide/messages.js";

const CONSEQUENCES: Record<string, () => string> = {
  browser: m.tool_consequence_acts_in_the_chats_browser_like_a_person,
  code_run: m.tool_consequence_runs_the_code_in_a_sandbox_that_can,
  computer:
    m.tool_consequence_restarting_ends_every_process_and_terminal_job_in,
  files: m.tool_consequence_write_and_edit_create_or_change_a_file,
  job: m.tool_consequence_cancelling_stops_the_jobs_work_they_have_not,
  request_capability:
    m.tool_consequence_enables_the_capability_for_this_chat_only_the,
  request_credential:
    m.tool_consequence_stores_the_entered_secret_for_this_chat_bound,
  screen: m.tool_consequence_moves_the_mouse_and_types_on_the_chats,
  terminal: m.tool_consequence_runs_the_command_in_the_computer_it_can,
  wakeup: m.tool_consequence_the_agent_starts_a_turn_by_itself_at,
  watch_draft: m.tool_consequence_stores_a_draft_source_or_a_revision_of,
  watch_history: m.tool_consequence_reads_stored_history_nothing_is_fetched,
  web_fetch: m.tool_consequence_loads_the_url_once_no_data_is_submitted,
};

/** The program text a call would run, when it carries one, shown as it would run. */
function programOf(
  args: unknown,
): { code: string; language?: string; keys: string[] } | null {
  if (!isRecord(args)) {
    return null;
  }
  if (typeof args.code === "string") {
    return {
      code: args.code,
      keys: ["code", "language"],
      language: typeof args.language === "string" ? args.language : undefined,
    };
  }
  if (typeof args.command === "string" && args.command.includes("\n")) {
    return { code: args.command, keys: ["command"], language: "bash" };
  }
  return null;
}

/**
 * What a failed decision left behind, in words, with a retry when one makes sense. A request
 * that is no longer waiting is also reported in a notice, because its card is about to go.
 */
function DecisionError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const note =
    error instanceof ApiError && error.status === 503
      ? m.approval_your_decision_may_be_recorded_but_not_yet()
      : null;
  return (
    <div className="mt-3 space-y-2">
      {note && <p className="font-medium">{note}</p>}
      <ErrorState error={error} onRetry={isGone(error) ? undefined : onRetry} />
    </div>
  );
}

/** What the card does not already say: the places the call names and the untouched record. */
function ApprovalDetails({
  action,
  tool,
  shownKeys = [],
}: {
  action: Action;
  tool: Tool | undefined;
  shownKeys?: string[];
}) {
  const args = action.arguments;
  const rest = isRecord(args)
    ? Object.fromEntries(
        Object.entries(args).filter(([key]) => !shownKeys.includes(key)),
      )
    : null;
  return (
    <Disclosure
      className="mt-1"
      contentClassName="space-y-2 pb-1"
      label={m.approval_call_details()}
    >
      <p className="text-muted-foreground text-xs">
        {m.approval_tool({ tool: action.tool })}
        {resourcesOf(action).map((resource) => (
          <span key={`${resource.kind}:${resource.value}`}>
            {" / "}
            {resource.kind}{" "}
            <span className="font-mono break-all">{resource.value}</span>
          </span>
        ))}
      </p>
      {rest ? (
        Object.keys(rest).length > 0 && (
          <ValueView schema={asSchema(tool?.input_schema)} value={rest} />
        )
      ) : (
        <JsonBlock value={args} />
      )}
      <p className="text-muted-foreground text-xs">
        {m.approval_runs_exactly_as_shown_secrets_are_redacted_allow()}
      </p>
      <RawJson value={action} />
    </Disclosure>
  );
}

function SensitiveStandingLink({ tool }: { tool: string }) {
  const notice = m.approval_sensitive_actions_are_allowed_one_at_a_time();
  const splitAt = notice.indexOf(". ");
  const lead = splitAt === -1 ? notice : notice.slice(0, splitAt + 1);
  const rest = splitAt === -1 ? "" : notice.slice(splitAt + 2);
  return (
    <p className="text-muted-foreground mt-2 text-sm">
      {lead}
      {rest !== "" && (
        <>
          {" "}
          <Link
            className="underline underline-offset-2"
            search={{ tool }}
            to="/guardrails/new"
          >
            {rest}
          </Link>
        </>
      )}
    </p>
  );
}

/**
 * The commitment point, kept short: what the agent wants to do, to what, what that affects
 * and why you are asked, then the three answers. Everything else is one click away under
 * Call details. Only a sensitive action is outlined in the attention colour; needing a
 * decision is not, by itself, a warning.
 */
export function ApprovalCard({
  action,
  chatTitle,
}: {
  action: Action;
  chatTitle?: string;
}) {
  const tools = useQuery(catalogQueries.tools());
  const decision = useApprovalDecision(action);
  const [choosingScope, setChoosingScope] = useState(false);
  const alwaysButton = useRef<HTMLButtonElement>(null);
  const returning = useRef(false);
  const tool = tools.data?.find((item) => item.name === action.tool);
  const program = programOf(action.arguments);
  const target = summarize(action);
  const busy = decision.isPending;
  const pending = busy ? decision.variables?.decision : undefined;
  const sensitive = action.risk === "sensitive";

  // Leaving the second step puts focus back on the button that opened it.
  useEffect(() => {
    if (!choosingScope && returning.current) {
      alwaysButton.current?.focus();
    }
    returning.current = false;
  }, [choosingScope]);

  return (
    <DecisionCard
      ariaLabel={m.approval_approval_needed({ title: actionTitle(action) })}
      busy={busy}
      emphasis={sensitive}
      footer={
        !choosingScope && (
          <>
            <Button
              className="h-11 sm:h-8"
              disabled={busy}
              onClick={() => decision.mutate({ decision: "approve" })}
              size="sm"
            >
              {pending === "approve"
                ? m.approval_allowing()
                : m.approval_allow_once()}
            </Button>
            {!sensitive && (
              <Button
                className="h-11 sm:h-8"
                disabled={busy}
                onClick={() => setChoosingScope(true)}
                ref={alwaysButton}
                size="sm"
                variant="outline"
              >
                {m.approval_always_allow()}
              </Button>
            )}
            <Button
              className="h-11 sm:h-8"
              disabled={busy}
              onClick={() => decision.mutate({ decision: "reject" })}
              size="sm"
              variant="outline"
            >
              {pending === "reject"
                ? m.approval_rejecting()
                : m.approval_reject()}
            </Button>
          </>
        )
      }
      icon={<IconShieldQuestion aria-hidden="true" />}
      label={
        <>
          {m.approval_needs_your_decision()}
          {action.risk !== "read" && (
            <>
              <span className="text-muted-foreground font-normal">—</span>
              <RiskLabel risk={action.risk} />
            </>
          )}
        </>
      }
      meta={timeAgo(action.created_at)}
    >
      <p className="text-base">
        <span className="font-medium">{actionTitle(action)}</span>
        {target && !program && (
          <span className="text-muted-foreground ml-2 font-mono text-sm break-all">
            {target}
          </span>
        )}
      </p>
      {tool?.consequences && (
        <p className="text-muted-foreground mt-0.5 text-sm">
          {CONSEQUENCES[action.tool]?.() ?? tool.consequences}
        </p>
      )}
      {program && (
        <CodeBlock
          className="mt-2"
          code={program.code}
          language={program.language}
          previewLines={6}
        />
      )}
      <p className="text-muted-foreground mt-1.5 text-xs">
        {action.rule_id
          ? m.approval_one_of_your_rules_asks()
          : m.approval_this_chats_mode_asks()}
        {chatTitle && (
          <>
            {" "}
            <Link
              className="underline underline-offset-2"
              params={{ chatId: action.chat_id }}
              to="/chats/$chatId"
            >
              {m.approval_open({ chat: chatTitle })}
            </Link>
          </>
        )}
      </p>
      {choosingScope && (
        <AlwaysAllowPanel
          action={action}
          onCancel={() => {
            returning.current = true;
            setChoosingScope(false);
          }}
          onConfirm={(scope, breadth, summary) =>
            decision.mutate({ breadth, decision: "approve", scope, summary })
          }
          pending={busy}
        />
      )}
      {sensitive && <SensitiveStandingLink tool={action.tool} />}
      {decision.isError && (
        <DecisionError
          error={decision.error}
          onRetry={() => {
            if (decision.variables) {
              decision.mutate(decision.variables);
            }
          }}
        />
      )}
      <ApprovalDetails action={action} shownKeys={program?.keys} tool={tool} />
    </DecisionCard>
  );
}
