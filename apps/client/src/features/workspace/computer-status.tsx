import { useQuery } from "@tanstack/react-query";
import type { ComputerStatus } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Skeleton } from "@/components/ui/skeleton.tsx";
import { workspaceQueries } from "@/features/workspace/api.ts";
import { bytes, timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const FAILURE: Record<
  ComputerStatus["failures"][number]["status"],
  () => string
> = {
  computer_failed: m.chat_the_computer_failed,
  full: m.chat_the_disk_was_full,
  lost: m.chat_the_workspace_was_lost,
};

const usedOf = (used: number, limit: number | null | undefined): string =>
  limit ? m.chat_of_2({ limit: bytes(limit), used: bytes(used) }) : bytes(used);

/** What the Computer is doing and what it uses, on one line, with recent failures below. */
function Health({ status }: { status: ComputerStatus }) {
  const { computer, workspace } = status;
  const running = computer?.status === "running";
  const usage: string[] = [];
  if (computer?.memory_bytes != null) {
    usage.push(
      m.chat_memory({
        x: usedOf(computer.memory_bytes, computer.memory_limit_bytes),
      }),
    );
  }
  if (computer?.cpus != null) {
    usage.push(m.chat_cpu({ n: computer.cpus }));
  }
  if (computer?.pids != null) {
    usage.push(m.chat_processes({ n: computer.pids }));
  }
  if (computer?.browser) {
    usage.push(m.chat_browser_open());
  }
  if (computer?.jobs) {
    usage.push(m.chat_terminal_jobs({ n: computer.jobs }));
  }
  if (workspace.status !== "none") {
    usage.push(
      m.chat_disk({
        x: usedOf(workspace.size_bytes, workspace.size_limit_bytes),
      }),
    );
  }
  const trouble: string[] = [];
  if (computer?.oom_killed) {
    trouble.push(m.chat_ran_out_of_memory());
  }
  if (computer && computer.restarts > 0) {
    trouble.push(m.chat_restarted({ n: computer.restarts }));
  }
  const state = running
    ? m.chat_running_2()
    : computer
      ? m.chat_computer_3({ status: computer.status })
      : m.chat_off();

  return (
    <div className="space-y-1">
      <StatusText
        className="font-medium"
        tone={running ? "live" : computer ? "attention" : "neutral"}
      >
        {state}
        {trouble.length > 0 && (
          <span className="text-attention font-normal">
            {" — "}
            {trouble.join(", ")}
          </span>
        )}
      </StatusText>
      {usage.length > 0 && (
        <p className="text-muted-foreground text-xs tabular-nums">
          {usage.join(" / ")}
        </p>
      )}
      {status.failures.slice(0, 3).map((failure) => (
        <p
          className="text-attention text-xs"
          key={`${failure.at}-${failure.status}`}
        >
          {FAILURE[failure.status]()} {timeAgo(failure.at)}
          {failure.reason ? `: ${failure.reason}` : ""}
        </p>
      ))}
    </div>
  );
}

/** Lifecycle and usage of the chat's Computer, refreshed while shown. */
export function ComputerStatusPanel({ chatId }: { chatId: string }) {
  const status = useQuery(workspaceQueries.computerStatus(chatId));
  if (status.isPending) {
    return <Skeleton className="h-10 w-56" />;
  }
  if (status.isError) {
    return (
      <ErrorState error={status.error} onRetry={() => void status.refetch()} />
    );
  }
  return <Health status={status.data} />;
}
