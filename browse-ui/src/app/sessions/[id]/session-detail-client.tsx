"use client";

import { useParams } from "next/navigation";

export function SessionDetailClient() {
  const params = useParams<{ id: string }>();
  return (
    <div>
      <h1 className="text-2xl font-semibold">Session: {params.id}</h1>
      <p className="mt-2 text-muted-foreground">Implementation in Pha 7</p>
    </div>
  );
}
