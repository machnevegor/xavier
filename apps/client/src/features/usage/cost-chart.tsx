import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import type { UsageDay } from "@/api/types.ts";
import { usd } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

/** Cost per UTC day; the table beside it carries the same numbers for assistive technology. */
export function CostChart({ days }: { days: UsageDay[] }) {
  return (
    <div aria-hidden="true" className="h-56 w-full text-xs">
      <BarChart
        data={days}
        responsive
        style={{ height: "100%", width: "100%" }}
      >
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis
          axisLine={false}
          dataKey="day"
          minTickGap={24}
          tick={{ fill: "var(--muted-foreground)" }}
          tickFormatter={(day: string) => day.slice(5)}
          tickLine={false}
        />
        <YAxis
          axisLine={false}
          tick={{ fill: "var(--muted-foreground)" }}
          tickFormatter={(value: number) => usd(value)}
          tickLine={false}
          width={48}
        />
        <Tooltip
          contentStyle={{
            background: "var(--popover)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            color: "var(--popover-foreground)",
          }}
          cursor={{ fill: "var(--muted)" }}
          formatter={(value) => [usd(Number(value)), m.usage_cost_3()]}
        />
        <Bar dataKey="cost_usd" fill="var(--chart-1)" radius={3} />
      </BarChart>
    </div>
  );
}
