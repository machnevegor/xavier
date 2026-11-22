import { queryOptions, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, readPage } from "@/api/client.ts";
import { allNumbered } from "@/api/query.ts";
import type { Tool } from "@/api/types.ts";

const SLOW = 5 * 60 * 1000;
const PAGE = 200;

export const catalogQueries = {
  models: () =>
    queryOptions({
      queryFn: ({ signal }) =>
        allNumbered((page) =>
          readPage(
            api.GET("/api/v1/models", {
              params: { query: { page, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: ["models"],
      staleTime: SLOW,
    }),
  tools: () =>
    queryOptions({
      queryFn: ({ signal }) =>
        allNumbered((page) =>
          readPage(
            api.GET("/api/v1/tools", {
              params: { query: { page, per_page: PAGE } },
              signal,
            }),
          ),
        ),
      queryKey: ["tools"],
      staleTime: SLOW,
    }),
};

export function useToolIndex(): Map<string, Tool> {
  const { data } = useQuery(catalogQueries.tools());
  return useMemo(
    () => new Map((data ?? []).map((tool) => [tool.name, tool])),
    [data],
  );
}
