import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { pagesOptions } from "@/api/query.ts";
import type { Execution, ExecutionCreate } from "@/api/types.ts";

export const isRunning = (execution: Execution | undefined): boolean =>
  execution?.status === "pending" || execution?.status === "running";

export const runQueries = {
  /** Accepted is not finished: a run is polled until it reaches a final status. */
  detail: (id: string) =>
    queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/executions/{execution_id}", {
            params: { path: { execution_id: id } },
            signal,
          }),
        ),
      queryKey: ["execution", id],
      refetchInterval: (query) => (isRunning(query.state.data) ? 1500 : false),
    }),
  list: () => ({
    ...pagesOptions(["executions"], (cursor, signal) =>
      readPage(
        api.GET("/api/v1/executions", {
          params: { query: { cursor, per_page: 50 } },
          signal,
        }),
      ),
    ),
    refetchInterval: 4000,
  }),
};

export const startRun = (body: ExecutionCreate) =>
  read(api.POST("/api/v1/executions", { body: { data: body } }));

export const cancelRun = (id: string) =>
  read(
    api.POST("/api/v1/executions/{execution_id}/cancel", {
      params: { path: { execution_id: id } },
    }),
  );
