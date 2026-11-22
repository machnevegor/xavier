import { useInfiniteQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { flatten } from "@/api/query.ts";
import type { ExecutionStatus } from "@/api/types.ts";
import { Page, PageHeader } from "@/components/page.tsx";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadMore,
} from "@/components/states.tsx";
import { Status, type StatusMeta } from "@/components/status-text.tsx";
import { runQueries } from "@/features/runs/api.ts";
import { EXECUTION_STATUS } from "@/features/runs/model.ts";
import { RunForm } from "@/features/runs/run-form.tsx";
import { between, timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/runs/")({
  component: RunsPage,
  head: () => ({ meta: [{ title: m.runs_code_runs_xavier() }] }),
});

function languageLabel(language: string): string {
  switch (language) {
    case "python":
      return m.run_python();
    case "node":
      return m.run_node();
    case "bash":
      return m.run_bash();
    default:
      return language;
  }
}

function runStatusLabel(status: ExecutionStatus): string {
  switch (status) {
    case "cancelled":
      return m.run_cancelled();
    case "failed":
      return m.run_failed();
    case "pending":
      return m.run_starting();
    case "running":
      return m.run_running();
    case "succeeded":
      return m.run_succeeded();
    case "timeout":
      return m.run_timed_out();
    case "uncertain":
      return m.run_outcome_unknown();
    default:
      return status;
  }
}

function runStatus(status: ExecutionStatus): StatusMeta {
  return { ...EXECUTION_STATUS[status], label: runStatusLabel(status) };
}

function RunsPage() {
  const runs = useInfiniteQuery(runQueries.list());
  const items = flatten(runs.data);

  return (
    <Page>
      <PageHeader
        lead={m.runs_run_a_short_script_in_isolation_without_a()}
        title={m.runs_code_runs()}
      />
      <RunForm />
      {runs.isPending ? (
        <LoadingRows />
      ) : runs.isError ? (
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={m.runs_no_runs_yet()}>
          {m.runs_your_runs_are_listed_here_with_their_output()}
        </EmptyState>
      ) : (
        <>
          <ul className="divide-y border-y">
            {items.map((run) => (
              <li key={run.id}>
                <Link
                  className="hover:bg-accent/50 flex min-h-11 flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3"
                  params={{ executionId: run.id }}
                  to="/runs/$executionId"
                >
                  <span className="min-w-0 flex-1 basis-56 truncate font-mono text-sm">
                    {(run.code ?? run.command ?? "").split("\n")[0]}
                  </span>
                  <span className="text-muted-foreground whitespace-nowrap">
                    {languageLabel(run.language)}
                    {run.chat_id ? ` ${m.runs_from_a_chat()}` : ""}
                  </span>
                  <Status meta={runStatus(run.status)} />
                  <span className="text-muted-foreground whitespace-nowrap tabular-nums sm:w-40 sm:text-right">
                    {between(run.started_at, run.finished_at)} —{" "}
                    {timeAgo(run.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <LoadMore {...runs} />
        </>
      )}
    </Page>
  );
}
