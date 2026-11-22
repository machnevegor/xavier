import { useEffect, useRef, useState } from "react";
import {
  type Action,
  type Breadth,
  isRecord,
  type Scope,
} from "@/api/types.ts";
import { Button } from "@/components/ui/button.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group.tsx";
import { m } from "@/paraglide/messages.js";

const TOOL_NAMES: Record<string, () => string> = {
  browser: m.action_use_the_browser,
  code_run: m.action_run_code,
  files: m.action_work_with_files,
  job: m.action_manage_a_running_job,
  request_capability: m.action_request_a_capability,
  request_credential: m.action_request_a_credential,
  screen: m.action_use_the_desktop,
  terminal: m.action_run_a_terminal_command,
  watch_draft: m.action_draft_a_watch,
  watch_history: m.action_read_watch_history,
  web_fetch: m.action_read_a_web_page,
};

interface Resource {
  kind: string;
  value: string;
}

/** The places a call names, as the backend extracted them for policy matching. */
export function resourcesOf(action: Action): Resource[] {
  return Array.isArray(action.resources)
    ? action.resources.flatMap((item) =>
        isRecord(item) &&
        typeof item.kind === "string" &&
        typeof item.value === "string"
          ? [{ kind: item.kind, value: item.value }]
          : [],
      )
    : [];
}

const EXACT = { label: m.approval_exactly_these, value: "exact" as const };

const BREADTH: Record<string, { value: Breadth; label: () => string }[]> = {
  command: [{ label: m.approval_only_this_command, value: "exact" }],
  host: [
    { label: m.approval_only_this_host, value: "exact" },
    { label: m.approval_this_host_and_its_subdomains, value: "subdomains" },
    { label: m.approval_any_public_website, value: "public_web" },
  ],
  path: [
    { label: m.approval_only_this_file, value: "exact" },
    { label: m.approval_this_folder_and_below, value: "prefix" },
  ],
  url: [
    { label: m.approval_only_this_address, value: "exact" },
    { label: m.approval_this_site_and_its_subdomains, value: "subdomains" },
    { label: m.approval_any_public_website, value: "public_web" },
  ],
};

function where(places: string, breadth: Breadth): string {
  switch (breadth) {
    case "public_web":
      return m.approval_any_public_website_2();
    case "subdomains":
      return m.approval_and_its_subdomains({ places });
    case "prefix":
      return m.approval_anything_under({ places });
    default:
      return places || m.approval_anything();
  }
}

/**
 * The second step of Always allow: where the permission applies and, when the call names a
 * place, how wide it reaches. The server saves the rule with the decision.
 */
export function AlwaysAllowPanel({
  action,
  pending,
  onConfirm,
  onCancel,
}: {
  action: Action;
  pending: boolean;
  onConfirm: (scope: Scope, breadth: Breadth, summary: string) => void;
  onCancel: () => void;
}) {
  const resources = resourcesOf(action);
  const kinds = [...new Set(resources.map((resource) => resource.kind))];
  const breadths =
    kinds.length === 1 ? (BREADTH[kinds[0] ?? ""] ?? [EXACT]) : [EXACT];
  const [breadth, setBreadth] = useState<Breadth>(
    breadths[0]?.value ?? "exact",
  );
  const [scope, setScope] = useState<Scope>("chat");
  const heading = useRef<HTMLParagraphElement>(null);
  useEffect(() => heading.current?.focus(), []);

  const places = resources.map((resource) => resource.value).join(", ");
  const tool = TOOL_NAMES[action.tool]?.() ?? action.tool;
  const whereText = where(places, breadth);
  const summary =
    scope === "chat"
      ? m.approval_on_runs_without_asking_in_this_chat({
          tool,
          where: whereText,
        })
      : m.approval_on_runs_without_asking_in_every_chat({
          tool,
          where: whereText,
        });
  const choices = breadths.map((item) => ({
    label: item.label(),
    value: item.value,
  }));

  return (
    <div className="animate-in fade-in-0 slide-in-from-top-1 mt-3 space-y-3 rounded-lg border p-3 duration-150">
      <p
        className="text-sm font-medium outline-none"
        ref={heading}
        tabIndex={-1}
      >
        {m.approval_allow_similar_calls_in_future()}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup
          aria-label={m.approval_where_it_applies()}
          onValueChange={([next]) => {
            if (next === "chat" || next === "always") {
              setScope(next);
            }
          }}
          size="sm"
          spacing={0}
          value={[scope]}
          variant="outline"
        >
          <ToggleGroupItem value="chat">
            {m.approval_for_this_chat()}
          </ToggleGroupItem>
          <ToggleGroupItem value="always">
            {m.approval_for_all_chats()}
          </ToggleGroupItem>
        </ToggleGroup>
        {breadths.length > 1 && (
          <Select
            items={choices}
            onValueChange={(next) => {
              const match = breadths.find((item) => item.value === next);
              if (match) {
                setBreadth(match.value);
              }
            }}
            value={breadth}
          >
            <SelectTrigger
              aria-label={m.approval_how_wide()}
              className="min-w-0"
              size="sm"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {choices.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      <p className="text-muted-foreground text-sm break-words">
        {summary}{" "}
        {scope === "chat"
          ? m.approval_you_can_remove_it_later_in_the_chats()
          : m.approval_you_can_remove_it_later_in_guardrails()}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={pending}
          onClick={() => onConfirm(scope, breadth, summary)}
          size="sm"
        >
          {pending ? m.approval_saving() : m.approval_always_allow_2()}
        </Button>
        <Button disabled={pending} onClick={onCancel} size="sm" variant="ghost">
          {m.common_cancel()}
        </Button>
      </div>
    </div>
  );
}
