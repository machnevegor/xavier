import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { flatten } from "@/api/query.ts";
import type { AlertKind } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { CopyButton } from "@/components/copy-button.tsx";
import { FormField } from "@/components/form-field.tsx";
import { EmptyState, ErrorState, QueryBoundary } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Checkbox } from "@/components/ui/checkbox.tsx";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog.tsx";
import {
  FieldDescription,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import {
  createSubscription,
  deleteSubscription,
  watchKeys,
  watchQueries,
} from "@/features/watch/api.ts";
import { ALERT_KINDS, alertKindLabel } from "@/features/watch/model.ts";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const ALL_SOURCES = "all";

function randomSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

function NewWebhookDialog({
  sources,
}: {
  sources: { label: string; value: string }[];
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [source, setSource] = useState(ALL_SOURCES);
  const [kinds, setKinds] = useState<AlertKind[]>([]);
  const items = [
    { label: m.watch_settings_every_source(), value: ALL_SOURCES },
    ...sources,
  ];
  const reset = () => {
    setUrl("");
    setSecret("");
    setSource(ALL_SOURCES);
    setKinds([]);
  };
  const create = useMutation({
    mutationFn: () =>
      createSubscription({
        channel: "webhook",
        kinds,
        secret,
        source_id: source === ALL_SOURCES ? null : source,
        url: url.trim(),
      }),
    onSuccess: async () => {
      change(false);
      await queryClient.invalidateQueries({
        queryKey: watchKeys.subscriptions,
      });
    },
  });
  function change(next: boolean) {
    setOpen(next);
    if (!next) {
      reset();
      create.reset();
    }
  }

  return (
    <Dialog onOpenChange={change} open={open}>
      <DialogTrigger render={<Button size="sm" variant="outline" />}>
        {m.watch_settings_add_a_webhook()}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {m.watch_settings_send_alerts_to_a_webhook()}
          </DialogTitle>
          <DialogDescription>
            {m.watch_settings_each_new_alert_is_posted_as_json_failed()}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-6"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <FormField id="webhook-url" label={m.watch_settings_address()}>
            <Input
              id="webhook-url"
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://"
              required
              type="url"
              value={url}
            />
          </FormField>
          <FormField
            help={m.watch_settings_16_to_200_characters_write_it_down_now()}
            id="webhook-secret"
            label={m.watch_settings_signing_secret()}
          >
            <div className="flex gap-2">
              <Input
                autoComplete="off"
                className="font-mono"
                id="webhook-secret"
                maxLength={200}
                minLength={16}
                onChange={(event) => setSecret(event.target.value)}
                required
                spellCheck={false}
                value={secret}
              />
              {secret ? (
                <CopyButton
                  label={m.watch_settings_copy_secret()}
                  value={secret}
                />
              ) : (
                <Button
                  onClick={() => setSecret(randomSecret())}
                  type="button"
                  variant="outline"
                >
                  {m.watch_settings_generate()}
                </Button>
              )}
            </div>
          </FormField>
          <FormField id="webhook-source" label={m.watch_settings_from()}>
            <Select
              items={items}
              onValueChange={(next) => setSource(next ?? ALL_SOURCES)}
              value={source}
            >
              <SelectTrigger className="w-full" id="webhook-source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {items.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>
          <FieldSet className="gap-3">
            <FieldLegend variant="label">
              {m.watch_settings_which_alerts()}
            </FieldLegend>
            <FieldDescription>
              {m.watch_settings_none_ticked_sends_every_kind()}
            </FieldDescription>
            <div
              className="grid gap-1 sm:grid-cols-2"
              data-slot="checkbox-group"
            >
              {ALERT_KINDS.map((kind) => (
                <label
                  className="flex min-h-9 cursor-pointer items-center gap-3"
                  htmlFor={`webhook-kind-${kind.value}`}
                  key={kind.value}
                >
                  <Checkbox
                    checked={kinds.includes(kind.value)}
                    id={`webhook-kind-${kind.value}`}
                    onCheckedChange={(on) =>
                      setKinds((current) =>
                        on
                          ? [...current, kind.value]
                          : current.filter((item) => item !== kind.value),
                      )
                    }
                  />
                  {kind.label()}
                </label>
              ))}
            </div>
          </FieldSet>
          {create.isError && <ErrorState error={create.error} />}
          <DialogFooter>
            <Button disabled={create.isPending} type="submit">
              {create.isPending
                ? m.watch_settings_saving()
                : m.watch_settings_add_webhook()}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Where alerts go besides this page. Signing secrets are write-only. */
export function Webhooks() {
  const queryClient = useQueryClient();
  const subscriptions = useQuery(watchQueries.subscriptions());
  const sources = useInfiniteQuery(watchQueries.sources());
  const sourceItems = flatten(sources.data).map((source) => ({
    label: source.name,
    value: source.id,
  }));
  const names = new Map(sourceItems.map((item) => [item.value, item.label]));
  const remove = useMutation({
    mutationFn: deleteSubscription,
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: watchKeys.subscriptions }),
  });

  return (
    <div className="space-y-3">
      <div>
        <NewWebhookDialog sources={sourceItems} />
      </div>
      {remove.isError && <ErrorState error={remove.error} />}
      <QueryBoundary query={subscriptions} rows={2}>
        {(all) => {
          const hooks = all.filter((item) => item.channel === "webhook");
          return hooks.length === 0 ? (
            <EmptyState title={m.watch_settings_no_webhooks()}>
              {m.watch_settings_alerts_are_listed_on_the_watch_page_add()}
            </EmptyState>
          ) : (
            <ul className="divide-y border-y">
              {hooks.map((hook) => (
                <li
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3"
                  key={hook.id}
                >
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="font-mono text-sm break-all">{hook.url}</p>
                    <p className="text-muted-foreground mt-0.5">
                      {hook.source_id
                        ? (names.get(hook.source_id) ??
                          m.watch_settings_one_source())
                        : m.watch_settings_every_source_2()}
                      {" / "}
                      {hook.kinds.length
                        ? hook.kinds.map(alertKindLabel).join(", ")
                        : m.watch_settings_every_kind()}
                      {" / "}
                      {m.watch_settings_added({
                        ago: timeAgo(hook.created_at),
                      })}
                    </p>
                  </div>
                  <ConfirmAction
                    confirmLabel={m.watch_settings_delete_webhook()}
                    description={m.watch_settings_alerts_stop_being_sent_to_this_address()}
                    onConfirm={() => remove.mutate(hook.id)}
                    title={m.watch_settings_delete_this_webhook()}
                    trigger={
                      <Button
                        disabled={remove.isPending}
                        size="sm"
                        variant="ghost"
                      >
                        {m.common_delete()}
                      </Button>
                    }
                  />
                </li>
              ))}
            </ul>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
