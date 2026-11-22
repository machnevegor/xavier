import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import type { Source, SourceInput } from "@/api/types.ts";
import { JsonBlock } from "@/components/code-block.tsx";
import { Disclosure } from "@/components/disclosure.tsx";
import { Breadcrumbs, Page, PageHeader, Section } from "@/components/page.tsx";
import { QueryBoundary } from "@/components/states.tsx";
import { Status } from "@/components/status-text.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet.tsx";
import { describeTiming } from "@/features/schedules/timing.ts";
import { AlertList } from "@/features/watch/alert-list.tsx";
import {
  replaceSource,
  watchKeys,
  watchQueries,
} from "@/features/watch/api.ts";
import { sourceStatus, specOf } from "@/features/watch/model.ts";
import { SourceActions } from "@/features/watch/source-actions.tsx";
import { SourceForm } from "@/features/watch/source-form.tsx";
import { VersionHistory } from "@/features/watch/version-history.tsx";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/watch/$sourceId")({
  component: SourcePage,
  head: () => ({ meta: [{ title: m.watch_source_source_xavier() }] }),
  loader: ({ context, params }) =>
    void context.queryClient.prefetchQuery(
      watchQueries.source(params.sourceId),
    ),
});

function SourcePage() {
  const { sourceId } = Route.useParams();
  const source = useQuery(watchQueries.source(sourceId));
  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: m.watch_source_watch(), to: "/watch" },
          { label: source.data?.name ?? m.watch_source_source() },
        ]}
      />
      <QueryBoundary query={source}>
        {(data) => <SourceDetails source={data} />}
      </QueryBoundary>
    </Page>
  );
}

/** Editing saves a draft, or a revision of a polled source; publishing makes it count. */
function EditSource({ source }: { source: Source }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const save = useMutation({
    mutationFn: (input: SourceInput) => replaceSource(source.id, input),
    onSuccess: async () => {
      setOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: watchKeys.source(source.id),
        }),
        queryClient.invalidateQueries({ queryKey: watchKeys.sources }),
      ]);
    },
  });
  const published = source.status !== "draft";
  return (
    <>
      <Button onClick={() => setOpen(true)} variant="outline">
        {m.watch_source_edit_rules()}
      </Button>
      <Sheet onOpenChange={setOpen} open={open}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>
              {m.watch_source_edit({ name: source.name })}
            </SheetTitle>
            <SheetDescription>
              {published
                ? m.watch_source_saving_creates_a_revision_checks_keep_using_the()
                : m.watch_source_saving_updates_the_draft_nothing_is_checked_until()}
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-6">
            {open && (
              <SourceForm
                error={save.error}
                name={source.name}
                onSubmit={(input) => save.mutate(input)}
                pending={save.isPending}
                spec={specOf(source.draft) ?? specOf(source.spec)}
                submitLabel={
                  published
                    ? m.watch_source_save_revision()
                    : m.watch_source_save_draft()
                }
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

function SourceDetails({ source }: { source: Source }) {
  const spec = specOf(source.spec);
  return (
    <>
      <div>
        <PageHeader title={source.name}>
          <EditSource source={source} />
        </PageHeader>
        <p className="text-muted-foreground mt-1 font-mono text-sm break-all">
          {spec?.url}
        </p>
        <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
          <Status meta={sourceStatus(source)} />
          {spec && <span>{describeTiming(spec.schedule)}</span>}
          <span>
            {source.last_polled_at
              ? m.watch_source_checked({ ago: timeAgo(source.last_polled_at) })
              : m.watch_source_not_checked_yet()}
          </span>
          {source.chat_id && (
            <Link
              className="underline underline-offset-2"
              params={{ chatId: source.chat_id }}
              to="/chats/$chatId"
            >
              {m.watch_source_drafted_in_a_chat()}
            </Link>
          )}
        </div>
        {source.last_error && (
          <p className="border-attention mt-3 border-l-2 pl-3" role="alert">
            {source.pause_reason === "auth_lost" ? (
              <Link
                className="underline underline-offset-2"
                to="/watch/settings"
              >
                {m.watch_source_checks_are_paused_because_the_site_rejected_this()}
              </Link>
            ) : (
              m.watch_source_last_check_failed()
            )}{" "}
            <span className="text-muted-foreground">{source.last_error}</span>
          </p>
        )}
      </div>
      <SourceActions source={source} />
      <Section title={m.watch_source_alerts()}>
        <AlertList sourceId={source.id} />
      </Section>
      <Section title={m.watch_source_what_changed()}>
        <VersionHistory sourceId={source.id} />
      </Section>
      <Section title={m.watch_source_rules()}>
        {source.draft !== null && (
          <div className="space-y-2">
            <p className="font-medium">
              {m.watch_source_waiting_to_be_published()}
            </p>
            <JsonBlock value={source.draft} />
          </div>
        )}
        {source.draft !== null ? (
          <Disclosure label={m.watch_source_published_rules()}>
            <div className="mt-2">
              <JsonBlock value={source.spec} />
            </div>
          </Disclosure>
        ) : (
          <JsonBlock value={source.spec} />
        )}
      </Section>
    </>
  );
}
