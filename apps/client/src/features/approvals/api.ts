import { api, read, readPage } from "@/api/client.ts";
import { pagesOptions } from "@/api/query.ts";
import type { Breadth, Decision, DecisionInput, Scope } from "@/api/types.ts";

export const approvalQueries = {
  waiting: () => ({
    ...pagesOptions(["approvals"], (cursor, signal) =>
      readPage(
        api.GET("/api/v1/approvals", {
          params: { query: { cursor, per_page: 50 } },
          signal,
        }),
      ),
    ),
    refetchInterval: 5000,
  }),
};

/** Repeating the same decision is safe; a different one on a decided action answers 409. */
export function decide(
  actionId: string,
  decision: Decision,
  scope: Scope = "once",
  breadth: Breadth = "exact",
) {
  const body: DecisionInput = { breadth, decision, scope };
  return read(
    api.POST("/api/v1/approvals/{action_id}/decision", {
      body: { data: body },
      params: { path: { action_id: actionId } },
    }),
  );
}
