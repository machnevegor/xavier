import createClient from "openapi-fetch";
import { apiError, isAbort, networkError } from "@/api/errors.ts";
import type { Page } from "@/api/query.ts";
import type { PaginationInfo } from "@/api/types.ts";
import type { paths } from "../../__generated__/schema.ts";

/**
 * The typed REST client. `/api` is served on the page's own origin, so requests carry the
 * HttpOnly session cookie and no token is ever held in the page.
 *
 * Repeated `filter[]` values are sent as repeated query parameters.
 */
export const api = createClient<paths>({
  querySerializer: {
    array: { explode: true, style: "form" },
    object: { explode: true, style: "deepObject" },
  },
});

interface Outcome<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Resolve a client call to its body, or throw an `ApiError` for any failure. */
export async function unwrap<T>(call: Promise<Outcome<T>>): Promise<T> {
  let outcome: Outcome<T>;
  try {
    outcome = await call;
  } catch (cause) {
    if (isAbort(cause)) {
      throw cause;
    }
    throw networkError(cause);
  }
  if (!outcome.response.ok) {
    throw apiError(outcome.response, outcome.error);
  }
  return outcome.data as T;
}

/** The `data` of a `{data, success}` result. */
export async function read<T>(call: Promise<Outcome<{ data: T }>>): Promise<T> {
  return (await unwrap(call)).data;
}

/** A list result as `{items, info}` from `{data, info, success}`. */
export async function readPage<T>(
  call: Promise<Outcome<{ data: T[]; info: PaginationInfo }>>,
): Promise<Page<T>> {
  const body = await unwrap(call);
  return { info: body.info, items: body.data };
}
