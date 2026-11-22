import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { allPages } from "@/api/query.ts";
import type { PolicyRuleInput } from "@/api/types.ts";

const ruleId = (id: string) => ({ path: { rule_id: id } });
const PAGE = 200;

async function rules(
  query: {
    "filter[chat_id]"?: string[];
    "filter[is_global]"?: boolean;
  },
  signal: AbortSignal,
) {
  return (
    await allPages((cursor) =>
      readPage(
        api.GET("/api/v1/policy/rules", {
          params: { query: { ...query, cursor, per_page: PAGE } },
          signal,
        }),
      ),
    )
  ).items;
}

export const policyQueries = {
  /** Rules of one chat. */
  chat: (chatId: string) =>
    queryOptions({
      queryFn: ({ signal }) => rules({ "filter[chat_id]": [chatId] }, signal),
      queryKey: ["rules", "chat", chatId],
    }),
  /** Rules that apply to every chat. */
  global: () =>
    queryOptions({
      queryFn: ({ signal }) => rules({ "filter[is_global]": true }, signal),
      queryKey: ["rules", "global"],
    }),
};

/** With an id the rule is replaced in place; without one it is added. */
export const saveRule = ({
  id,
  input,
}: {
  id?: string;
  input: PolicyRuleInput;
}) =>
  id
    ? read(
        api.PATCH("/api/v1/policy/rules/{rule_id}", {
          body: { data: input },
          params: ruleId(id),
        }),
      )
    : read(api.POST("/api/v1/policy/rules", { body: { data: input } }));

export const deleteRule = (id: string) =>
  read(api.DELETE("/api/v1/policy/rules/{rule_id}", { params: ruleId(id) }));
