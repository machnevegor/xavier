import { IconChevronRight } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import type { Effect, Mode } from "@/api/types.ts";
import { SchemaReference } from "@/components/data-view.tsx";
import { Page, PageHeader } from "@/components/page.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table.tsx";
import { RiskLabel } from "@/features/actions/risk-label.tsx";
import { catalogQueries } from "@/features/catalog/api.ts";
import { toolLabel } from "@/features/catalog/model.ts";
import { MODES } from "@/features/chats/model.ts";
import { RuleList } from "@/features/policy/rule-list.tsx";
import { MODE_DEFAULT } from "@/features/policy/semantics.ts";
import { asSchema } from "@/lib/schema.ts";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/guardrails/")({
  component: GuardrailsPage,
  head: () => ({ meta: [{ title: m.guardrails_guardrails_xavier() }] }),
});

function modeLabel(mode: Mode): string {
  switch (mode) {
    case "supervised":
      return m.chat_supervised();
    case "autonomous":
      return m.chat_autonomous();
    case "read_only":
      return m.chat_read_only();
  }
}

function effectLabel(effect: Effect): string {
  switch (effect) {
    case "allow":
      return m.rule_allow();
    case "ask":
      return m.rule_ask_me();
    case "deny":
      return m.rule_block();
  }
}

function catalogLabel(name: string): string {
  switch (name) {
    case "browser":
      return m.action_use_the_browser();
    case "code_run":
      return m.action_run_code();
    case "files":
      return m.action_work_with_files();
    case "job":
      return m.action_manage_a_running_job();
    case "request_capability":
      return m.action_request_a_capability();
    case "request_credential":
      return m.action_request_a_credential();
    case "screen":
      return m.action_use_the_desktop();
    case "terminal":
      return m.action_run_a_terminal_command();
    case "watch_draft":
      return m.action_draft_a_watch();
    case "watch_history":
      return m.action_read_watch_history();
    case "web_fetch":
      return m.action_read_a_web_page();
    default:
      return toolLabel(name);
  }
}

function toolConsequence(name: string, fallback: string): string {
  switch (name) {
    case "code_run":
      return m.tool_consequence_runs_the_code_in_a_sandbox_that_can();
    case "web_fetch":
      return m.tool_consequence_loads_the_url_once_no_data_is_submitted();
    case "watch_draft":
      return m.tool_consequence_stores_a_draft_source_or_a_revision_of();
    case "watch_history":
      return m.tool_consequence_reads_stored_history_nothing_is_fetched();
    case "job":
      return m.tool_consequence_cancelling_stops_the_jobs_work_they_have_not();
    case "files":
      return m.tool_consequence_write_and_edit_create_or_change_a_file();
    case "browser":
      return m.tool_consequence_acts_in_the_chats_browser_like_a_person();
    case "screen":
      return m.tool_consequence_moves_the_mouse_and_types_on_the_chats();
    case "terminal":
      return m.tool_consequence_runs_the_command_in_the_computer_it_can();
    case "computer":
      return m.tool_consequence_restarting_ends_every_process_and_terminal_job_in();
    case "request_credential":
      return m.tool_consequence_stores_the_entered_secret_for_this_chat_bound();
    case "wakeup":
      return m.tool_consequence_the_agent_starts_a_turn_by_itself_at();
    case "request_capability":
      return m.tool_consequence_enables_the_capability_for_this_chat_only_the();
    default:
      return fallback;
  }
}

function toolDescription(name: string, fallback: string): string {
  switch (name) {
    case "code_run":
      return m.tool_description_run_python_node_or_bash_code_in_a();
    case "web_fetch":
      return m.tool_description_fetch_a_public_web_page_or_api_response();
    case "watch_draft":
      return m.tool_description_draft_deterministic_monitoring_of_an_api_or_web();
    case "watch_history":
      return m.tool_description_read_what_an_observe_source_recorded_the_latest();
    case "job":
      return m.tool_description_check_wait_for_or_cancel_jobs_that_code();
    case "files":
      return m.tool_description_work_with_files_in_the_chat_workspace_which();
    case "browser":
      return m.tool_description_use_the_chromium_browser_of_the_chats_computer();
    case "screen":
      return m.tool_description_see_and_control_the_desktop_of_the_chats();
    case "terminal":
      return m.tool_description_run_a_bash_command_in_the_chats_computer();
    case "computer":
      return m.tool_description_status_reports_the_chats_computer_and_workspace_cpu();
    case "request_credential":
      return m.tool_description_ask_the_user_to_enter_a_login_api();
    case "wakeup":
      return m.tool_description_schedule_a_later_turn_for_yourself_in_this();
    case "request_capability":
      return m.tool_description_ask_the_user_to_enable_a_capability_for();
    default:
      return fallback;
  }
}

function ToolReference() {
  const tools = useQuery(catalogQueries.tools());
  if (tools.isPending) {
    return <LoadingRows />;
  }
  if (tools.isError) {
    return (
      <ErrorState error={tools.error} onRetry={() => void tools.refetch()} />
    );
  }
  return (
    <ul className="divide-y border-y">
      {tools.data.map((tool) => (
        <li key={tool.name}>
          <Collapsible>
            <CollapsibleTrigger className="group flex min-h-12 w-full items-center gap-3 py-2 text-left font-medium hover:underline">
              <IconChevronRight
                aria-hidden="true"
                className="text-muted-foreground size-4 shrink-0 transition-transform group-data-panel-open:rotate-90"
              />
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span>{catalogLabel(tool.name)}</span>
                <RiskLabel risk={tool.risk} />
                <span className="text-muted-foreground font-mono text-xs font-normal">
                  {tool.name}
                </span>
              </span>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-3 pb-4 pl-7">
              <p>{toolDescription(tool.name, tool.description)}</p>
              <p className="text-muted-foreground">
                {toolConsequence(tool.name, tool.consequences)}
              </p>
              <div>
                <h3 className="text-muted-foreground mb-1.5 text-xs font-medium">
                  {m.guardrails_what_it_is_given()}
                </h3>
                <SchemaReference schema={asSchema(tool.input_schema)} />
              </div>
            </CollapsibleContent>
          </Collapsible>
        </li>
      ))}
    </ul>
  );
}

function GuardrailsPage() {
  return (
    <Page>
      <PageHeader
        lead={m.guardrails_before_the_agent_does_anything_xavier_checks_whether()}
        title={m.guardrails_guardrails()}
      >
        <Button nativeButton={false} render={<Link to="/guardrails/new" />}>
          {m.guardrails_add_a_rule()}
        </Button>
      </PageHeader>

      <section aria-labelledby="rules-heading" className="space-y-3">
        <div>
          <h2 className="type-section" id="rules-heading">
            {m.guardrails_your_rules()}
          </h2>
          <p className="text-muted-foreground mt-1">
            {m.guardrails_permissions_you_saved_with_always_allow_in_a()}
          </p>
        </div>
        <RuleList />
      </section>

      <section aria-labelledby="modes-heading">
        <h2 className="type-section mb-1" id="modes-heading">
          {m.guardrails_when_no_rule_applies()}
        </h2>
        <p className="text-muted-foreground mb-3">
          {m.guardrails_each_chat_has_a_mode_set_when_you()}
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{m.guardrails_chat_mode()}</TableHead>
              <TableHead>{m.guardrails_looking_at_things()}</TableHead>
              <TableHead>{m.guardrails_changing_things()}</TableHead>
              <TableHead>{m.guardrails_sensitive_actions()}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {MODES.map(({ value }) => (
              <TableRow key={value}>
                <TableCell className="font-medium">
                  {modeLabel(value)}
                </TableCell>
                <TableCell>{effectLabel(MODE_DEFAULT[value].read)}</TableCell>
                <TableCell>{effectLabel(MODE_DEFAULT[value].write)}</TableCell>
                <TableCell>
                  {effectLabel(MODE_DEFAULT[value].sensitive)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <section aria-labelledby="tools-heading">
        <h2 className="type-section mb-1" id="tools-heading">
          {m.guardrails_what_the_agent_can_do()}
        </h2>
        <p className="text-muted-foreground mb-3">
          {m.guardrails_every_action_a_rule_can_be_about_and()}
        </p>
        <ToolReference />
      </section>
    </Page>
  );
}
