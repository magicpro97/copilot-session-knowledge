"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { EmbeddingPoint } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const CATEGORY_COLORS: Record<string, string> = {
  mistake: "#ff6b6b",
  pattern: "#51cf66",
  decision: "#339af0",
  discovery: "#cc5de8",
  feature: "#fcc419",
  refactor: "#ff922b",
  tool: "#20c997",
};

const DEFAULT_POINT_COLOR = "#9ca3af";

type ScatterCanvasProps = {
  points: EmbeddingPoint[];
  selectedPointId?: number | null;
  className?: string;
  onPointSelect?: (point: EmbeddingPoint | null) => void;
};

type ProjectedPoint = {
  point: EmbeddingPoint;
  px: number;
  py: number;
};

function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? DEFAULT_POINT_COLOR;
}

export function ScatterCanvas({
  points,
  selectedPointId = null,
  className,
  onPointSelect,
}: ScatterCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hoveredPoint, setHoveredPoint] = useState<ProjectedPoint | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const node = containerRef.current;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({
        width: Math.max(240, Math.floor(entry.contentRect.width)),
        height: Math.max(320, Math.floor(entry.contentRect.height)),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const projectedPoints = useMemo<ProjectedPoint[]>(() => {
    if (!points.length || size.width === 0 || size.height === 0) return [];

    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    for (const point of points) {
      if (point.x < minX) minX = point.x;
      if (point.x > maxX) maxX = point.x;
      if (point.y < minY) minY = point.y;
      if (point.y > maxY) maxY = point.y;
    }

    const rangeX = Math.max(maxX - minX, 1e-6);
    const rangeY = Math.max(maxY - minY, 1e-6);
    const padding = 24;
    const drawWidth = Math.max(size.width - padding * 2, 1);
    const drawHeight = Math.max(size.height - padding * 2, 1);

    return points.map((point) => ({
      point,
      px: padding + ((point.x - minX) / rangeX) * drawWidth,
      py: size.height - (padding + ((point.y - minY) / rangeY) * drawHeight),
    }));
  }, [points, size.height, size.width]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(size.width * dpr);
    canvas.height = Math.floor(size.height * dpr);
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;

    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size.width, size.height);

    for (const projected of projectedPoints) {
      const isSelected = projected.point.id === selectedPointId;
      context.beginPath();
      context.arc(projected.px, projected.py, isSelected ? 4 : 2.2, 0, Math.PI * 2);
      context.fillStyle = categoryColor(projected.point.category);
      context.fill();
      if (isSelected) {
        context.lineWidth = 1.5;
        context.strokeStyle = "rgba(17, 24, 39, 0.9)";
        context.stroke();
      }
    }
  }, [projectedPoints, selectedPointId, size.height, size.width]);

  useEffect(() => {
    if (!hoveredPoint) return;
    if (!projectedPoints.some((item) => item.point.id === hoveredPoint.point.id)) {
      setHoveredPoint(null);
    }
  }, [hoveredPoint, projectedPoints]);

  const findNearest = (x: number, y: number): ProjectedPoint | null => {
    let best: ProjectedPoint | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const projected of projectedPoints) {
      const dx = projected.px - x;
      const dy = projected.py - y;
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = projected;
      }
    }
    return bestDistance <= 64 ? best : null;
  };

  return (
    <div
      ref={containerRef}
      className={cn("relative h-[65vh] min-h-[22rem] w-full rounded-xl border bg-card", className)}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full"
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const nearest = findNearest(event.clientX - rect.left, event.clientY - rect.top);
          setHoveredPoint(nearest);
        }}
        onMouseLeave={() => setHoveredPoint(null)}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const nearest = findNearest(event.clientX - rect.left, event.clientY - rect.top);
          onPointSelect?.(nearest?.point ?? null);
        }}
      />

      {hoveredPoint ? (
        <div
          className="pointer-events-none absolute z-10 max-w-72 rounded-md border bg-background/95 px-2 py-1 text-xs shadow"
          style={{
            left: Math.min(hoveredPoint.px + 12, Math.max(size.width - 300, 0)),
            top: Math.max(hoveredPoint.py - 12, 8),
          }}
        >
          <p className="font-medium text-foreground">{hoveredPoint.point.title}</p>
          <p className="text-muted-foreground">{hoveredPoint.point.category}</p>
        </div>
      ) : null}
    </div>
  );
}
