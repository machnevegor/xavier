import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { type Execution, type ExecutionStatus, isRecord } from "@/api/types.ts";
import { CodeBlock } from "@/components/code-block.tsx";
import { Breadcrumbs, Page, PageHeader, Section } from "@/components/page.tsx";
import { ErrorState, QueryBoundary } from "@/components/states.tsx";
import { Status, type StatusMeta } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { ArtifactChips } from "@/features/artifacts/artifact-list.tsx";
import { cancelRun, isRunning, runQueries } from "@/features/runs/api.ts";
import { EXECUTION_STATUS } from "@/features/runs/model.ts";
import { between, dateTime } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/runs/$executionId")({
  component: RunPage,
  head: () => ({ meta: [{ title: m.run_run_xavier() }] }),
  loader: ({ context, params }) =>
    void context.queryClient.prefetchQuery(
      runQueries.detail(params.executionId),
    ),
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

function runNote(status: ExecutionStatus): string | undefined {
  if (status === "timeout") {
    return m.run_the_run_exceeded_its_time_limit_and_was();
  }
  if (status === "uncertain") {
    return m.run_the_worker_was_lost_while_this_ran_so();
  }
  return undefined;
}

function RunPage() {
  const { executionId } = Route.useParams();
  const run = useQuery(runQueries.detail(executionId));
  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: m.run_code_runs(), to: "/runs" },
          { label: m.run_run() },
        ]}
      />
      <QueryBoundary query={run}>
        {(data) => <RunDetails run={data} />}
      </QueryBoundary>
    </Page>
  );
}

function RunDetails({ run }: { run: Execution }) {
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => cancelRun(run.id),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["execution", run.id] }),
        queryClient.invalidateQueries({ queryKey: ["executions"] }),
      ]),
  });
  const running = isRunning(run);
  const note = runNote(run.status);
  const started = run.started_at ?? run.created_at;
  const missing =
    isRecord(run.metrics) && Array.isArray(run.metrics.missing_exports)
      ? run.metrics.missing_exports.filter((path) => typeof path === "string")
      : [];

  return (
    <>
      <div>
        <PageHeader
          title={m.run_run_2({ language: languageLabel(run.language) })}
        >
          {running && (
            <Button
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
              variant="outline"
            >
              {cancel.isPending ? m.run_cancelling() : m.run_cancel_run()}
            </Button>
          )}
        </PageHeader>
        <div
          aria-live="polite"
          className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1"
        >
          <Status meta={runStatus(run.status)} />
          <span>{m.run_started({ date: dateTime(started) })}</span>
          {run.finished_at && (
            <span>
              {m.run_took({ duration: between(started, run.finished_at) })}
            </span>
          )}
          {run.exit_code !== null && (
            <span>{m.run_exit_code({ code: run.exit_code })}</span>
          )}
          <span>
            {run.network ? m.run_with_internet() : m.run_no_internet()}
          </span>
          {run.chat_id && (
            <Link
              className="underline underline-offset-2"
              params={{ chatId: run.chat_id }}
              to="/chats/$chatId"
            >
              {m.run_open_its_chat()}
            </Link>
          )}
        </div>
        {note && (
          <p className="border-attention mt-3 border-l-2 pl-3">{note}</p>
        )}
        {run.error && (
          <p className="border-attention mt-3 border-l-2 pl-3">{run.error}</p>
        )}
        {cancel.isError && (
          <div className="mt-3">
            <ErrorState error={cancel.error} />
          </div>
        )}
      </div>
      <Section title={run.code ? m.run_code() : m.run_command()}>
        <CodeBlock
          code={run.code ?? run.command ?? ""}
          language={run.code ? run.language : "bash"}
        />
      </Section>
      <Section
        title={
          run.truncated
            ? `${m.run_output()} ${m.run_truncated_at_the_output_limit()}`
            : m.run_output()
        }
      >
        <CodeBlock
          code={
            run.stdout ||
            (running ? m.run_the_run_is_still_in_progress() : m.run_no_output())
          }
          label={m.run_output()}
        />
      </Section>
      {run.stderr && (
        <Section title={m.run_errors()}>
          <CodeBlock
            className={
              run.status === "failed"
                ? "border-destructive rounded-l-none border-l-2"
                : undefined
            }
            code={run.stderr}
            label={m.run_errors()}
          />
        </Section>
      )}
      {(run.input_artifact_ids.length > 0 || run.export.length > 0) && (
        <Section title={m.run_files()}>
          {run.input_artifact_ids.length > 0 && (
            <div className="space-y-2">
              <p className="text-muted-foreground">
                {m.run_given_to_the_run()}
              </p>
              <ArtifactChips ids={run.input_artifact_ids} />
            </div>
          )}
          {run.output_artifact_ids.length > 0 && (
            <div className="space-y-2">
              <p className="text-muted-foreground">
                {m.run_kept_after_the_run()}
              </p>
              <ArtifactChips ids={run.output_artifact_ids} />
            </div>
          )}
          {missing.length > 0 && (
            <p className="text-attention">
              {m.run_not_found_when_the_run_ended_so_not()}{" "}
              <span className="font-mono">{missing.join(", ")}</span>
            </p>
          )}
        </Section>
      )}
    </>
  );
}
