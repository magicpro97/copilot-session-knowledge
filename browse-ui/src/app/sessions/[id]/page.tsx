import { SessionDetailClient } from "./session-detail-client";

// Required for Next.js static export — returns empty array since session IDs
// are dynamic; unknown IDs handled by SPA fallback in serve_v2.py
export async function generateStaticParams() {
  return [{ id: "_placeholder" }];
}

export default function SessionDetailPage() {
  return <SessionDetailClient />;
}
