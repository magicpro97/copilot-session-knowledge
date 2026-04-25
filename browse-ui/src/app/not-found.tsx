import Link from "next/link";
import { Compass, Search, ScrollText } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[calc(100vh-10rem)] w-full max-w-2xl items-center justify-center">
      <Card className="w-full border-dashed">
        <CardHeader className="space-y-3">
          <div className="inline-flex w-fit items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-xs font-medium text-muted-foreground">
            <Compass className="size-3.5" />
            404 · Route not found
          </div>
          <CardTitle className="text-2xl">This route is not available.</CardTitle>
          <CardDescription>
            The page may have moved, or the URL is invalid. Use a known route to recover quickly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Link href="/sessions" className={cn(buttonVariants({ variant: "default" }))}>
              <ScrollText className="size-4" />
              Go to Sessions
            </Link>
            <Link
              href="/search"
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              <Search className="size-4" />
              Open Search
            </Link>
          </div>
          <p className="text-xs text-muted-foreground">
            Tip: press <kbd className="rounded border px-1 font-mono text-[10px]">⌘K</kbd>{" "}
            (or <kbd className="rounded border px-1 font-mono text-[10px]">Ctrl+K</kbd>) for
            command palette navigation.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
