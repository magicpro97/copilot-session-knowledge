import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
      <div className="bg-card space-y-3 rounded-xl border p-3">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <div className="bg-card rounded-xl border p-3">
        <Skeleton className="h-[65vh] w-full" />
      </div>
    </div>
  );
}
