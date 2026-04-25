import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
      <div className="space-y-3 rounded-xl border bg-card p-3">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <div className="rounded-xl border bg-card p-3">
        <Skeleton className="h-[65vh] w-full" />
      </div>
    </div>
  );
}
