"use client";

import { useState, useEffect } from "react";

export type Density = "comfortable" | "compact";

export function useDensity(): [Density, (d: Density) => void] {
  const [density, setDensityState] = useState<Density>("comfortable");

  useEffect(() => {
    const stored = localStorage.getItem("browse-density") as Density | null;
    if (stored === "compact" || stored === "comfortable") {
      setDensityState(stored);
    }
  }, []);

  useEffect(() => {
    const html = document.documentElement;
    html.classList.remove("density-compact", "density-comfortable");
    if (density === "compact") html.classList.add("density-compact");
  }, [density]);

  const setDensity = (d: Density) => {
    localStorage.setItem("browse-density", d);
    setDensityState(d);
  };

  return [density, setDensity];
}
