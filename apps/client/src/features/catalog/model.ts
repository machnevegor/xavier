import { m } from "@/paraglide/messages.js";

type Text = () => string;

/** Plain names for the agent's tools; a tool the backend adds later shows its own name. */
const TOOL_LABELS: Record<string, Text> = {
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

export const toolLabel = (name: string): string =>
  TOOL_LABELS[name]?.() ?? name;

/**
 * From the tool reference (docs/tools.md): the catalog carries each tool's base risk only,
 * so the cases where a call is rated sensitive are recorded here.
 */
export const SENSITIVE_WHEN: Record<string, Text> = {
  browser: m.action_typing_into_a_password_field,
  watch_draft: m.action_widening_a_published_watch_to_new_sites_another,
};

const CONSEQUENCES: Record<string, Text> = {
  browser: m.tool_consequence_acts_in_the_chats_browser_like_a_person,
  code_run: m.tool_consequence_runs_the_code_in_a_sandbox_that_can,
  computer:
    m.tool_consequence_restarting_ends_every_process_and_terminal_job_in,
  files: m.tool_consequence_write_and_edit_create_or_change_a_file,
  job: m.tool_consequence_cancelling_stops_the_jobs_work_they_have_not,
  request_capability:
    m.tool_consequence_enables_the_capability_for_this_chat_only_the,
  request_credential:
    m.tool_consequence_stores_the_entered_secret_for_this_chat_bound,
  screen: m.tool_consequence_moves_the_mouse_and_types_on_the_chats,
  terminal: m.tool_consequence_runs_the_command_in_the_computer_it_can,
  wakeup: m.tool_consequence_the_agent_starts_a_turn_by_itself_at,
  watch_draft: m.tool_consequence_stores_a_draft_source_or_a_revision_of,
  watch_history: m.tool_consequence_reads_stored_history_nothing_is_fetched,
  web_fetch: m.tool_consequence_loads_the_url_once_no_data_is_submitted,
};

const DESCRIPTIONS: Record<string, Text> = {
  browser: m.tool_description_use_the_chromium_browser_of_the_chats_computer,
  code_run: m.tool_description_run_python_node_or_bash_code_in_a,
  computer:
    m.tool_description_status_reports_the_chats_computer_and_workspace_cpu,
  files: m.tool_description_work_with_files_in_the_chat_workspace_which,
  job: m.tool_description_check_wait_for_or_cancel_jobs_that_code,
  request_capability:
    m.tool_description_ask_the_user_to_enable_a_capability_for,
  request_credential: m.tool_description_ask_the_user_to_enter_a_login_api,
  screen: m.tool_description_see_and_control_the_desktop_of_the_chats,
  terminal: m.tool_description_run_a_bash_command_in_the_chats_computer,
  wakeup: m.tool_description_schedule_a_later_turn_for_yourself_in_this,
  watch_draft:
    m.tool_description_draft_deterministic_monitoring_of_an_api_or_web,
  watch_history:
    m.tool_description_read_what_an_observe_source_recorded_the_latest,
  web_fetch: m.tool_description_fetch_a_public_web_page_or_api_response,
};

/** Catalog consequence in the active language, or the backend string for an unknown tool. */
export const toolConsequence = (name: string, fallback: string): string =>
  CONSEQUENCES[name]?.() ?? fallback;

/** Catalog description in the active language, or the backend string for an unknown tool. */
export const toolDescription = (name: string, fallback: string): string =>
  DESCRIPTIONS[name]?.() ?? fallback;

/** Tools whose calls name no website or file, so resource patterns never match them. */
export const NO_PLACE = new Set(["watch_history", "job", "screen", "terminal"]);
