import { queryOptions } from "@tanstack/react-query";
import { api, read } from "@/api/client.ts";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** The period ends at the next full hour, so the query key stays stable while the page is open. */
export function usagePeriod(days: number, now: Date = new Date()) {
  const end = Math.ceil((now.getTime() + 1) / HOUR) * HOUR;
  return {
    end: new Date(end).toISOString(),
    start: new Date(end - days * DAY).toISOString(),
  };
}

export const usageQueries = {
  summary: (days: number) => {
    const period = usagePeriod(days);
    return queryOptions({
      queryFn: ({ signal }) =>
        read(
          api.GET("/api/v1/metrics/summary", {
            params: { query: period },
            signal,
          }),
        ),
      queryKey: ["metrics", period],
    });
  },
};
