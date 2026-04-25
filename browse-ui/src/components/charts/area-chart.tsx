"use client";

import {
  Area,
  AreaChart as RechartsAreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartContainer } from "@/components/charts/chart-container";

type AreaChartProps<TData extends Record<string, unknown>> = {
  data: TData[];
  xKey: keyof TData;
  yKey: keyof TData;
  title?: string;
  description?: string;
};

export function AreaChart<TData extends Record<string, unknown>>({
  data,
  xKey,
  yKey,
  title,
  description,
}: AreaChartProps<TData>) {
  return (
    <ChartContainer title={title} description={description}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart data={data}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/70" />
          <XAxis dataKey={String(xKey)} className="fill-muted-foreground text-xs" />
          <YAxis className="fill-muted-foreground text-xs" />
          <Tooltip />
          <Area
            type="monotone"
            dataKey={String(yKey)}
            stroke="hsl(var(--chart-1))"
            fill="hsl(var(--chart-1) / 0.2)"
            strokeWidth={2}
          />
        </RechartsAreaChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
