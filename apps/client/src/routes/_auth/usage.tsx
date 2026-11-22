import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { cn } from "cn";
import type { ReactNode } from "react";
import { Disclosure } from "@/components/disclosure.tsx";
import { Page, PageHeader, Section } from "@/components/page.tsx";
import { QueryBoundary } from "@/components/states.tsx";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table.tsx";
import { usageQueries } from "@/features/usage/api.ts";
import { CostChart } from "@/features/usage/cost-chart.tsx";
import { count, seconds, usd } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

const PERIODS = [7, 30, 90] as const;
type Period = (typeof PERIODS)[number];

const isPeriod = (value: unknown): value is Period =>
  PERIODS.some((period) => period === Number(value));

export const Route = createFileRoute("/_auth/usage")({
  component: UsagePage,
  head: () => ({ meta: [{ title: m.usage_usage_xavier() }] }),
  validateSearch: (search: Record<string, unknown>): { days?: Period } =>
    isPeriod(search.days) && Number(search.days) !== 7
      ? { days: Number(search.days) as Period }
      : {},
});

const total = (map: Record<string, number | undefined>) =>
  Object.values(map).reduce<number>((sum, n) => sum + (n ?? 0), 0);

function turnWord(key: string): string {
  switch (key) {
    case "completed":
      return m.usage_completed();
    case "failed":
      return m.usage_failed();
    case "interrupted":
      return m.usage_interrupted();
    case "limited":
      return m.usage_limited();
    case "running":
      return m.usage_running();
    case "stopped":
      return m.usage_stopped();
    case "suspended":
      return m.usage_suspended();
    default:
      return key;
  }
}

const breakdown = (map: Record<string, number | undefined>) =>
  Object.entries(map)
    .filter(([, n]) => n)
    .map(([key, n]) => `${count(n)} ${turnWord(key)}`)
    .join(" / ") || m.usage_none();

function Stat({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: ReactNode;
}) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="type-figure mt-1">{value}</dd>
      <dd className="text-muted-foreground mt-1 text-xs">{detail}</dd>
    </div>
  );
}

function UsagePage() {
  const { days = 7 } = Route.useSearch();
  const metrics = useQuery(usageQueries.summary(days));

  return (
    <Page>
      <PageHeader
        lead={m.usage_what_the_agents_work_cost_in_tokens_money()}
        title={m.usage_usage()}
      >
        <nav aria-label={m.usage_period()} className="flex gap-4">
          {PERIODS.map((period) => (
            <Link
              aria-current={period === days ? "page" : undefined}
              className={cn(
                "flex min-h-11 items-center border-b-2",
                period === days
                  ? "border-primary font-medium"
                  : "text-muted-foreground hover:text-foreground border-transparent",
              )}
              key={period}
              search={period === 7 ? {} : { days: period }}
              to="/usage"
            >
              {m.usage_days({ count: period })}
            </Link>
          ))}
        </nav>
      </PageHeader>
      <QueryBoundary query={metrics} rows={4}>
        {(data) => (
          <>
            <dl className="grid grid-cols-2 gap-6 md:grid-cols-4">
              <Stat
                detail={m.usage_chats({ count: data.chats })}
                label={m.usage_cost()}
                value={usd(data.cost_usd)}
              />
              <Stat
                detail={breakdown(data.turns)}
                label={m.usage_turns()}
                value={count(total(data.turns))}
              />
              <Stat
                detail={m.usage_in_out_cached({
                  cached: count(data.cache_read_tokens),
                  in: count(data.input_tokens),
                  out: count(data.output_tokens),
                })}
                label={m.usage_tokens()}
                value={count(data.input_tokens + data.output_tokens)}
              />
              <Stat
                detail={m.usage_code_runs({
                  count: total(data.executions),
                  time: seconds(data.execution_seconds),
                })}
                label={m.usage_agent_time()}
                value={seconds(data.turn_seconds)}
              />
            </dl>
            <Section title={m.usage_cost_per_day()}>
              {data.days.length === 0 ? (
                <p className="text-muted-foreground">
                  {m.usage_no_activity_in_this_period()}
                </p>
              ) : (
                <>
                  <CostChart days={data.days} />
                  <Disclosure
                    contentClassName="mt-3 overflow-x-auto"
                    label={m.usage_show_as_a_table()}
                  >
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{m.usage_day()}</TableHead>
                          <TableHead className="text-right">
                            {m.usage_turns_2()}
                          </TableHead>
                          <TableHead className="text-right">
                            {m.usage_tokens_in()}
                          </TableHead>
                          <TableHead className="text-right">
                            {m.usage_tokens_out()}
                          </TableHead>
                          <TableHead className="text-right">
                            {m.usage_cached()}
                          </TableHead>
                          <TableHead className="text-right">
                            {m.usage_cost_2()}
                          </TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.days.map((day) => (
                          <TableRow key={day.day}>
                            <TableCell>{day.day}</TableCell>
                            <TableCell className="text-right">
                              {count(day.turns)}
                            </TableCell>
                            <TableCell className="text-right">
                              {count(day.input_tokens)}
                            </TableCell>
                            <TableCell className="text-right">
                              {count(day.output_tokens)}
                            </TableCell>
                            <TableCell className="text-right">
                              {count(day.cache_read_tokens)}
                            </TableCell>
                            <TableCell className="text-right">
                              {usd(day.cost_usd)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Disclosure>
                </>
              )}
            </Section>
          </>
        )}
      </QueryBoundary>
    </Page>
  );
}
