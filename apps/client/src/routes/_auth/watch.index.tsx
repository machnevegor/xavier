import { useInfiniteQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { flatten } from "@/api/query.ts";
import { Page, PageHeader, Section } from "@/components/page.tsx";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  LoadMore,
} from "@/components/states.tsx";
import { Status } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import { AlertList } from "@/features/watch/alert-list.tsx";
import { watchQueries } from "@/features/watch/api.ts";
import { sourceStatus, specOf } from "@/features/watch/model.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/watch/")({
  component: WatchPage,
  head: () => ({ meta: [{ title: m.watch_watch_xavier() }] }),
});

function WatchPage() {
  const sources = useInfiniteQuery(watchQueries.sources());
  const items = flatten(sources.data);
  const names = useMemo(
    () => new Map(items.map((source) => [source.id, source.name])),
    [items],
  );

  return (
    <Page>
      <PageHeader
        lead={m.watch_pages_and_apis_are_checked_on_a_schedule()}
        title={m.watch_watch()}
      >
        <Button
          nativeButton={false}
          render={<Link to="/watch/settings" />}
          variant="outline"
        >
          {m.watch_credentials_and_webhooks()}
        </Button>
        <Button nativeButton={false} render={<Link to="/watch/new" />}>
          {m.watch_watch_something()}
        </Button>
      </PageHeader>

      <Section title={m.watch_alerts()}>
        <AlertList sourceNames={names} />
      </Section>

      <Section title={m.watch_sources()}>
        {sources.isPending ? (
          <LoadingRows />
        ) : sources.isError ? (
          <ErrorState
            error={sources.error}
            onRetry={() => void sources.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState title={m.watch_nothing_is_being_watched()}>
            {m.watch_add_an_address_yourself_or_ask_the_agent()}
          </EmptyState>
        ) : (
          <>
            <ul className="divide-y border-y">
              {items.map((source) => (
                <li key={source.id}>
                  <Link
                    className="hover:bg-accent/50 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 py-3.5"
                    params={{ sourceId: source.id }}
                    to="/watch/$sourceId"
                  >
                    <span className="min-w-0 flex-1 basis-56">
                      <span className="block truncate font-medium">
                        {source.name}
                      </span>
                      <span className="text-muted-foreground block truncate font-mono text-xs">
                        {specOf(source.spec)?.url}
                      </span>
                    </span>
                    <Status meta={sourceStatus(source)} />
                    <span className="text-muted-foreground text-right whitespace-nowrap sm:w-44">
                      {source.last_polled_at
                        ? m.watch_checked({
                            ago: timeAgo(source.last_polled_at),
                          })
                        : m.watch_not_checked_yet()}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <LoadMore {...sources} />
          </>
        )}
      </Section>
    </Page>
  );
}
