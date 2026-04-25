"use client";

import { useEffect } from "react";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";

export default function SessionDetailError({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="space-y-3">
      <Banner
        tone="danger"
        title="Session detail failed to render"
        description={error.message || "Unknown error"}
      />
      <Button variant="outline" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
