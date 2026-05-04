"use client";

import { createContext, useContext } from "react";
import type { HostProfile } from "@/lib/api/types";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { InsightsTabKey } from "./overview-tab";

type InsightsTabContextValue = {
  setActiveTab: (tab: InsightsTabKey) => void;
  /**
   * Whether diagnostics/insights API calls are safe to fire.
   * False when running on a hosted static origin with no remote agent host selected.
   * Defaults to false so components never fire same-origin 404s in static hosting.
   */
  diagnosticsEnabled: boolean;
  /** The currently active host profile (LOCAL_HOST when no remote host is selected). */
  host: HostProfile;
};

export const InsightsTabContext = createContext<InsightsTabContextValue>({
  setActiveTab: () => {},
  diagnosticsEnabled: false,
  host: LOCAL_HOST,
});

export function useInsightsTab(): InsightsTabContextValue {
  return useContext(InsightsTabContext);
}
