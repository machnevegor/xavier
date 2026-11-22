import { createFileRoute } from "@tanstack/react-router";
import { Breadcrumbs, Page, PageHeader, Section } from "@/components/page.tsx";
import { Credentials } from "@/features/watch/credentials.tsx";
import { Webhooks } from "@/features/watch/webhooks.tsx";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/watch/settings")({
  component: WatchSettingsPage,
  head: () => ({
    meta: [{ title: m.watch_settings_credentials_and_webhooks_xavier() }],
  }),
});

function WatchSettingsPage() {
  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: m.watch_settings_watch(), to: "/watch" },
          { label: m.watch_settings_credentials_and_webhooks() },
        ]}
      />
      <PageHeader
        lead={m.watch_settings_credentials_sources_use_for_signed_in_sites_and()}
        title={m.watch_settings_credentials_and_webhooks_2()}
      />
      <Section title={m.watch_settings_credentials()}>
        <Credentials />
      </Section>
      <Section title={m.watch_settings_webhooks()}>
        <Webhooks />
      </Section>
    </Page>
  );
}
