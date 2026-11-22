import { queryOptions } from "@tanstack/react-query";
import { api, read, readPage } from "@/api/client.ts";
import { allNumbered } from "@/api/query.ts";
import type { ScheduleInput } from "@/api/types.ts";

export type ScheduleCommand = "pause" | "resume" | "trigger";

export const scheduleQueries = {
  list: () =>
    queryOptions({
      queryFn: ({ signal }) =>
        allNumbered((page) =>
          readPage(
            api.GET("/api/v1/schedules", {
              params: { query: { page, per_page: 200 } },
              signal,
            }),
          ),
        ),
      queryKey: ["schedules"],
      refetchInterval: 10_000,
    }),
};

export const createSchedule = (body: ScheduleInput) =>
  read(api.POST("/api/v1/schedules", { body: { data: body } }));

export const deleteSchedule = (id: string) =>
  read(
    api.DELETE("/api/v1/schedules/{schedule_id}", {
      params: { path: { schedule_id: id } },
    }),
  );

/** `trigger` posts the message now, outside the timing; the run appears in its chat. */
export function runScheduleCommand(id: string, command: ScheduleCommand) {
  const params = { params: { path: { schedule_id: id } } };
  switch (command) {
    case "pause":
      return read(api.POST("/api/v1/schedules/{schedule_id}/pause", params));
    case "resume":
      return read(api.POST("/api/v1/schedules/{schedule_id}/resume", params));
    case "trigger":
      return read(api.POST("/api/v1/schedules/{schedule_id}/trigger", params));
    default:
      return command satisfies never;
  }
}
