"use client";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";

type SettingsErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function SettingsError({ error, reset }: SettingsErrorProps) {
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <Banner
        tone="danger"
        title={error.message || "We hit an unexpected error while rendering settings"}
        description="Retry the page. If this continues, verify the API server is reachable."
        actions={
          <Button type="button" variant="outline" size="sm" onClick={reset}>
            Retry
          </Button>
        }
      />
    </div>
  );
}
