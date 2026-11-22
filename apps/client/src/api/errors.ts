import { m } from "@/paraglide/messages.js";

/** One invalid value from a 422 response. */
export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** A failed request. `status` 0 means nothing answered. */
export class ApiError extends Error {
  readonly status: number;
  readonly details: ValidationIssue[] | null;

  constructor(
    status: number,
    message: string,
    details: ValidationIssue[] | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isIssue(value: unknown): value is ValidationIssue {
  return (
    isRecord(value) &&
    Array.isArray(value.loc) &&
    typeof value.msg === "string" &&
    typeof value.type === "string"
  );
}

/** Build an `ApiError` from a non-OK response body (`{message, details?}`). */
export function apiError(response: Response, body: unknown): ApiError {
  const message =
    isRecord(body) && typeof body.message === "string"
      ? body.message
      : response.statusText || m.error_request_failed();
  const details =
    isRecord(body) && Array.isArray(body.details)
      ? body.details.filter(isIssue)
      : null;
  return new ApiError(response.status, message, details);
}

export function networkError(cause: unknown): ApiError {
  return new ApiError(
    0,
    cause instanceof Error ? cause.message : m.error_cannot_reach_xavier(),
  );
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export const isUnauthorized = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 401;

const STATUS: Partial<
  Record<number, { title: () => string; hint: () => string }>
> = {
  401: {
    hint: m.error_sign_in_to_continue,
    title: m.error_not_signed_in_to_xavier,
  },
  404: { hint: m.error_it_may_have_been_deleted, title: m.error_not_found },
  409: {
    hint: m.error_the_item_is_in_a_state_that_does,
    title: m.error_that_is_not_possible_right_now,
  },
  413: {
    hint: m.error_the_file_is_larger_than_the_api_can,
    title: m.error_file_too_large,
  },
  503: {
    hint: m.error_one_of_its_services_is_unavailable_requests_will,
    title: m.error_xavier_is_temporarily_unavailable,
  },
};

/** Copy that depends on the server's message, not only the status. */
const BY_MESSAGE: {
  match: string;
  title: () => string;
  hint: () => string;
}[] = [
  {
    hint: m.error_it_was_decided_elsewhere_cancelled_or_it_expired,
    match: "Action already decided",
    title: m.error_this_request_no_longer_needs_a_decision,
  },
  {
    hint: m.error_raise_the_budget_in_the_chat_settings_then,
    match: "Chat budget exhausted",
    title: m.error_this_chat_has_reached_its_budget,
  },
  {
    hint: m.error_deleted_chats_are_final_start_a_new_chat,
    match: "Chat is deleted",
    title: m.error_this_chat_was_deleted,
  },
  {
    hint: m.error_the_agent_is_in_the_middle_of_a,
    match: "Computer is busy",
    title: m.error_the_computer_is_busy,
  },
  {
    hint: m.error_wake_the_workspace_to_view_its_files_and,
    match: "Workspace is hibernated",
    title: m.error_the_workspace_is_hibernated,
  },
  {
    hint: m.error_enter_your_current_password_and_try_again,
    match: "wrong current password",
    title: m.error_current_password_is_incorrect,
  },
];

/** An error in words a person can act on: what happened and what to do next. */
export function explain(error: unknown): { title: string; hint: string } {
  if (!(error instanceof ApiError)) {
    return {
      hint: error instanceof Error ? error.message : "",
      title: m.error_something_went_wrong(),
    };
  }
  if (error.status === 0) {
    return {
      hint: m.error_the_backend_did_not_answer_check_that_it(),
      title: m.error_cannot_reach_xavier(),
    };
  }
  const specific = BY_MESSAGE.find((item) =>
    error.message.includes(item.match),
  );
  if (specific) {
    return { hint: specific.hint(), title: specific.title() };
  }
  if (error.status === 422) {
    return {
      hint: error.message || m.error_check_the_values_and_try_again(),
      title: m.error_check_your_request(),
    };
  }
  const known = STATUS[error.status];
  return known
    ? { hint: known.hint(), title: known.title() }
    : {
        hint: "",
        title: error.message || m.error_something_went_wrong(),
      };
}
