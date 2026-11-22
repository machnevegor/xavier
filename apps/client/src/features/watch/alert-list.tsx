import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { flatten } from "@/api/query.ts";
import type { AlertStatus } from "@/api/types.ts";
import { Disclosure } from "@/components/disclosure.tsx";
import { ErrorState, LoadingRows, LoadMore } from "@/components/states.tsx";
import { StatusText } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  type AlertCommand,
  runAlertCommand,
  watchKeys,
  watchQueries,
} from "@/features/watch/api.ts";
import { alertKindLabel, resourceLabel } from "@/features/watch/model.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

interface ListProps {
  sourceId?: string | undefined;
  sourceNames?: Map<string, string> | undefined;
}

function AlertRows({
  status,
  sourceId,
  sourceNames,
  empty,
}: ListProps & { status: AlertStatus; empty: string }) {
  const queryClient = useQueryClient();
  const alerts = useInfiniteQuery(watchQueries.alerts(status, sourceId));
  const command = useMutation({
    mutationFn: ({ id, name }: { id: string; name: AlertCommand }) =>
      runAlertCommand(id, name),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: watchKeys.alerts }),
  });
  const items = flatten(alerts.data);

  if (alerts.isPending) {
    return <LoadingRows rows={2} />;
  }
  if (alerts.isError) {
    return (
      <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
    );
  }
  if (items.length === 0) {
    return <p className="text-muted-foreground">{empty}</p>;
  }
  return (
    <div className="space-y-3">
      {command.isError && <ErrorState error={command.error} />}
      <ul className="divide-y border-y">
        {items.map((alert) => (
          <li
            className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3"
            key={alert.id}
          >
            <div className="min-w-0 flex-1 basis-64">
              <p className="flex flex-wrap items-center gap-x-3">
                <StatusText tone={status === "open" ? "attention" : "neutral"}>
                  {alertKindLabel(alert.kind)}
                </StatusText>
                {!sourceId && (
                  <Link
                    className="font-medium underline-offset-2 hover:underline"
                    params={{ sourceId: alert.source_id }}
                    to="/watch/$sourceId"
                  >
                    {sourceNames?.get(alert.source_id) ?? m.watch_source()}
                  </Link>
                )}
                {alert.resource_key !== "" && (
                  <span className="text-muted-foreground truncate font-mono text-xs">
                    {resourceLabel(alert.resource_key)}
                  </span>
                )}
              </p>
              <p className="text-muted-foreground mt-0.5">
                {alert.message} — {timeAgo(alert.updated_at)}
                {alert.occurrences > 1
                  ? ` — ${m.watch_seen_times({ count: alert.occurrences })}`
                  : ""}
              </p>
            </div>
            <div className="flex gap-2">
              {status === "open" && (
                <Button
                  disabled={command.isPending}
                  onClick={() =>
                    command.mutate({ id: alert.id, name: "acknowledge" })
                  }
                  size="sm"
                  variant="outline"
                >
                  {m.watch_acknowledge()}
                </Button>
              )}
              <Button
                disabled={command.isPending}
                onClick={() =>
                  command.mutate({ id: alert.id, name: "resolve" })
                }
                size="sm"
                variant="outline"
              >
                {m.watch_resolve()}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <LoadMore {...alerts} />
    </div>
  );
}

/**
 * Unresolved alerts, newest first: open ones in full, acknowledged ones a click away. With
 * `sourceId`, only that source's.
 */
export function AlertList({ sourceId, sourceNames }: ListProps) {
  return (
    <div className="space-y-2">
      <AlertRows
        empty={m.watch_no_open_alerts_changes_outages_and_lost_access()}
        sourceId={sourceId}
        sourceNames={sourceNames}
        status="open"
      />
      <Disclosure label={m.watch_acknowledged_not_yet_resolved()}>
        <div className="mt-2">
          <AlertRows
            empty={m.watch_none()}
            sourceId={sourceId}
            sourceNames={sourceNames}
            status="acknowledged"
          />
        </div>
      </Disclosure>
    </div>
  );
}
