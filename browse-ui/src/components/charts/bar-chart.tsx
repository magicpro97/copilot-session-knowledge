"use client";

import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartContainer } from "@/components/charts/chart-container";

type BarChartProps<TData extends Record<string, unknown>> = {
  data: TData[];
  xKey: keyof TData;
  yKey: keyof TData;
  title?: string;
  description?: string;
};

export function BarChart<TData extends Record<string, unknown>>({
  data,
  xKey,
  yKey,
  title,
  description,
}: BarChartProps<TData>) {
  return (
    <ChartContainer title={title} description={description}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsBarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/70" />
          <XAxis dataKey={String(xKey)} className="fill-muted-foreground text-xs" />
          <YAxis className="fill-muted-foreground text-xs" />
          <Tooltip />
          <Bar dataKey={String(yKey)} fill="hsl(var(--chart-2))" radius={[6, 6, 0, 0]} />
        </RechartsBarChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
