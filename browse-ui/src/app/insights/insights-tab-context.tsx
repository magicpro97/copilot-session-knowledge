"use client";

import { createContext, useContext } from "react";
import type { InsightsTabKey } from "./overview-tab";

type InsightsTabContextValue = {
  setActiveTab: (tab: InsightsTabKey) => void;
};

export const InsightsTabContext = createContext<InsightsTabContextValue>({
  setActiveTab: () => {},
});

export function useInsightsTab(): InsightsTabContextValue {
  return useContext(InsightsTabContext);
}
