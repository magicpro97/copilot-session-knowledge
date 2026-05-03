import { Suspense } from "react";
import type { Metadata } from "next";
import { ChatShell } from "@/components/chat/chat-shell";

export const metadata: Metadata = {
  title: "Chat — Hindsight",
  description: "Run Copilot CLI prompts against a workspace and review the results.",
};

export default function ChatPage() {
  return (
    // Negative margins cancel out the main layout's padding so ChatShell gets
    // a full-bleed area for its own split-panel layout.
    <div className="-m-4 h-[calc(100%+2rem)] overflow-hidden md:-m-6 md:h-[calc(100%+3rem)]">
      <Suspense
        fallback={
          <div className="flex h-full items-center justify-center">
            <p className="text-muted-foreground animate-pulse text-sm">Loading…</p>
          </div>
        }
      >
        <ChatShell />
      </Suspense>
    </div>
  );
}
