import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { SourceInput } from "@/api/types.ts";
import { Breadcrumbs, Page, PageHeader } from "@/components/page.tsx";
import { createSource, watchKeys } from "@/features/watch/api.ts";
import { SourceForm } from "@/features/watch/source-form.tsx";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/watch/new")({
  component: NewSourcePage,
  head: () => ({ meta: [{ title: m.watch_new_new_source_watch_xavier() }] }),
});

function NewSourcePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: (input: SourceInput) => createSource(input),
    onSuccess: async (source) => {
      await queryClient.invalidateQueries({ queryKey: watchKeys.sources });
      await navigate({
        params: { sourceId: source.id },
        to: "/watch/$sourceId",
      });
    },
  });

  return (
    <Page className="max-w-3xl space-y-6">
      <Breadcrumbs
        items={[
          { label: m.watch_new_watch(), to: "/watch" },
          { label: m.watch_new_new_source() },
        ]}
      />
      <PageHeader
        lead={m.watch_new_this_creates_a_draft_nothing_is_checked_until()}
        title={m.watch_new_new_source_2()}
      />
      <SourceForm
        error={create.error}
        onSubmit={(input) => create.mutate(input)}
        pending={create.isPending}
        spec={null}
        submitLabel={m.watch_new_create_draft()}
      />
    </Page>
  );
}
