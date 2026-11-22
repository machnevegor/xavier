import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import type { SourceInput, SourceSpec } from "@/api/types.ts";
import { FormField } from "@/components/form-field.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import {
  buildTiming,
  describeTiming,
  emptyTiming,
  type TimingForm,
} from "@/features/schedules/timing.ts";
import { TimingEditor } from "@/features/schedules/timing-editor.tsx";
import { watchQueries } from "@/features/watch/api.ts";
import { m } from "@/paraglide/messages.js";

const MODES = [
  { label: m.watch_new_plain_request, value: "http" },
  { label: m.watch_new_open_in_a_browser, value: "browser" },
] as const;
const NO_CREDENTIAL = "none";

/** The backend's defaults for everything this form does not edit. */
const NEW_SPEC: Omit<SourceSpec, "url" | "schedule"> = {
  body: null,
  children: [],
  credential_id: null,
  extract: null,
  headers: {},
  limits: {},
  method: "GET",
  mode: "http",
  normalize: { ignore: [], sort: [] },
  render_wait_s: 2,
};

interface Fields {
  name: string;
  url: string;
  mode: SourceSpec["mode"];
  selector: string;
  attribute: string;
  ignore: string;
  credential: string;
}

function fieldsOf(name: string, spec: SourceSpec | null): Fields {
  return {
    attribute: spec?.extract?.attribute ?? "",
    credential: spec?.credential_id ?? NO_CREDENTIAL,
    ignore: (spec?.normalize?.ignore ?? []).join("\n"),
    mode: spec?.mode ?? "http",
    name,
    selector: spec?.extract?.jsonpath ?? spec?.extract?.css ?? "",
    url: spec?.url ?? "",
  };
}

function extractOf(fields: Fields): SourceSpec["extract"] {
  const selector = fields.selector.trim();
  if (!selector) {
    return null;
  }
  if (selector.startsWith("$")) {
    return { jsonpath: selector };
  }
  return { attribute: fields.attribute.trim() || null, css: selector };
}

const NEW_SOURCE_TIMING: TimingForm = {
  ...emptyTiming(),
  every: "hours",
  kind: "repeat",
};

/**
 * The everyday parts of a source. Everything else in an existing specification (child rules,
 * limits, headers, sorting) is kept as it is; the agent can draft those in a chat.
 */
export function SourceForm({
  name: initialName = "",
  spec,
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  name?: string;
  spec: SourceSpec | null;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (input: SourceInput) => void;
}) {
  const credentials = useQuery(watchQueries.credentials());
  const [fields, setFields] = useState(() => fieldsOf(initialName, spec));
  const [timing, setTiming] = useState<TimingForm | null>(
    spec ? null : NEW_SOURCE_TIMING,
  );
  const [attempted, setAttempted] = useState(false);
  const set = (patch: Partial<Fields>) =>
    setFields((current) => ({ ...current, ...patch }));
  const built = timing ? buildTiming(timing) : null;
  const timingError =
    attempted || timing?.kind === "cron" ? built?.error : undefined;
  const modeItems = MODES.map((item) => ({
    label: item.label(),
    value: item.value,
  }));
  const credentialItems = [
    { label: m.watch_new_none(), value: NO_CREDENTIAL },
    ...(credentials.data ?? []).map((item) => ({
      label: item.name,
      value: item.id,
    })),
  ];
  const css =
    fields.selector.trim() !== "" && !fields.selector.trim().startsWith("$");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    const schedule: SourceSpec["schedule"] | undefined = built
      ? built.timing && {
          cron: built.timing.cron ?? null,
          interval: null,
          time_zone: built.timing.time_zone ?? "UTC",
        }
      : spec?.schedule;
    if (!schedule) {
      return;
    }
    onSubmit({
      name: fields.name.trim(),
      spec: {
        ...NEW_SPEC,
        ...spec,
        credential_id:
          fields.credential === NO_CREDENTIAL ? null : fields.credential,
        extract: extractOf(fields),
        method: fields.mode === "browser" ? "GET" : (spec?.method ?? "GET"),
        mode: fields.mode,
        normalize: {
          ignore: fields.ignore
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
          sort: spec?.normalize.sort ?? [],
        },
        schedule,
        url: fields.url.trim(),
      },
    });
  };

  return (
    <form className="space-y-6" onSubmit={submit}>
      <FormField id="source-name" label={m.watch_new_name()}>
        <Input
          id="source-name"
          maxLength={100}
          onChange={(event) => set({ name: event.target.value })}
          required
          value={fields.name}
        />
      </FormField>
      <FormField
        help={m.watch_new_a_public_http_or_https_address_internal_addresses()}
        id="source-url"
        label={m.watch_new_address()}
      >
        <Input
          id="source-url"
          onChange={(event) => set({ url: event.target.value })}
          placeholder="https://"
          required
          type="url"
          value={fields.url}
        />
      </FormField>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField id="source-mode" label={m.watch_new_how_to_fetch()}>
          <Select
            items={modeItems}
            onValueChange={(next) => {
              const mode = MODES.find((item) => item.value === next);
              if (mode) {
                set({ mode: mode.value });
              }
            }}
            value={fields.mode}
          >
            <SelectTrigger className="w-full" id="source-mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {modeItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormField>
        <FormField
          help={m.watch_new_added_to_every_request_its_secret_is_never()}
          id="source-credential"
          label={m.watch_new_sign_in()}
        >
          <Select
            items={credentialItems}
            onValueChange={(next) => set({ credential: next ?? NO_CREDENTIAL })}
            value={fields.credential}
          >
            <SelectTrigger className="w-full" id="source-credential">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {credentialItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormField>
      </div>
      <FormField
        help={m.watch_new_a_jsonpath_starting_with_for_apis_or_a()}
        id="source-selector"
        label={m.watch_new_what_to_compare_optional()}
      >
        <Input
          autoCapitalize="none"
          className="font-mono text-sm"
          id="source-selector"
          onChange={(event) => set({ selector: event.target.value })}
          spellCheck={false}
          value={fields.selector}
        />
      </FormField>
      {css && (
        <FormField
          help={m.watch_new_compare_this_attribute_of_the_element_such_as()}
          id="source-attribute"
          label={m.watch_new_attribute_optional()}
        >
          <Input
            autoCapitalize="none"
            className="font-mono text-sm"
            id="source-attribute"
            onChange={(event) => set({ attribute: event.target.value })}
            spellCheck={false}
            value={fields.attribute}
          />
        </FormField>
      )}
      <FormField
        help={m.watch_new_jsonpath_of_fields_that_change_without_meaning_anything()}
        id="source-ignore"
        label={m.watch_new_ignore_optional()}
      >
        <Textarea
          className="font-mono text-sm"
          id="source-ignore"
          onChange={(event) => set({ ignore: event.target.value })}
          rows={2}
          spellCheck={false}
          value={fields.ignore}
        />
      </FormField>
      {timing ? (
        <TimingEditor
          allowOnce={false}
          error={timingError}
          form={timing}
          set={(patch) =>
            setTiming((current) => current && { ...current, ...patch })
          }
        />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <p>
            <span className="font-medium">{m.watch_new_when()} </span>
            {spec ? describeTiming(spec.schedule) : m.common_text()}
          </p>
          <Button
            onClick={() =>
              setTiming({
                ...NEW_SOURCE_TIMING,
                timeZone: spec?.schedule.time_zone ?? "UTC",
              })
            }
            size="sm"
            type="button"
            variant="outline"
          >
            {m.watch_new_change()}
          </Button>
        </div>
      )}
      {error ? <ErrorState error={error} /> : null}
      <Button disabled={pending} type="submit">
        {pending ? m.watch_new_saving() : submitLabel}
      </Button>
    </form>
  );
}
