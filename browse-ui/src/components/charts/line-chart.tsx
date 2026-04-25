"use client";

import {
  CartesianGrid,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartContainer } from "@/components/charts/chart-container";

type LineChartProps<TData extends Record<string, unknown>> = {
  data: TData[];
  xKey: keyof TData;
  yKey: keyof TData;
  title?: string;
  description?: string;
};

export function LineChart<TData extends Record<string, unknown>>({
  data,
  xKey,
  yKey,
  title,
  description,
}: LineChartProps<TData>) {
  return (
    <ChartContainer title={title} description={description}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsLineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/70" />
          <XAxis dataKey={String(xKey)} className="fill-muted-foreground text-xs" />
          <YAxis className="fill-muted-foreground text-xs" />
          <Tooltip />
          <Line
            type="monotone"
            dataKey={String(yKey)}
            stroke="hsl(var(--chart-3))"
            strokeWidth={2}
            dot={{ r: 2 }}
            activeDot={{ r: 4 }}
          />
        </RechartsLineChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
