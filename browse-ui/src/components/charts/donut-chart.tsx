"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { ChartContainer } from "@/components/charts/chart-container";

type DonutChartProps<TData extends Record<string, unknown>> = {
  data: TData[];
  nameKey: keyof TData;
  valueKey: keyof TData;
  title?: string;
  description?: string;
};

const COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

export function DonutChart<TData extends Record<string, unknown>>({
  data,
  nameKey,
  valueKey,
  title,
  description,
}: DonutChartProps<TData>) {
  return (
    <ChartContainer title={title} description={description}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Tooltip />
          <Pie
            data={data}
            dataKey={String(valueKey)}
            nameKey={String(nameKey)}
            innerRadius={70}
            outerRadius={105}
            paddingAngle={2}
          >
            {data.map((entry, index) => (
              <Cell
                key={`donut-${String(entry[nameKey])}-${index}`}
                fill={COLORS[index % COLORS.length]}
              />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
    </ChartContainer>
  );
}
