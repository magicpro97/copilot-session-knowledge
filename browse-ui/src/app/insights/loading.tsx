import { Skeleton } from "@/components/ui/skeleton";

export default function InsightsLoading() {
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={`insights-kpi-skeleton-${index}`} className="rounded-xl border bg-card p-4">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="mt-4 h-8 w-28" />
          </div>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border bg-card p-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-2 h-3 w-72 max-w-full" />
          <Skeleton className="mt-4 h-64 w-full" />
        </div>
        <div className="rounded-xl border bg-card p-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="mt-2 h-3 w-72 max-w-full" />
          <Skeleton className="mt-4 h-64 w-full" />
        </div>
      </div>

      <div className="rounded-xl border bg-card p-4">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="mt-2 h-3 w-80 max-w-full" />
        <div className="mt-4 space-y-2">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={`insights-table-skeleton-${index}`} className="h-8 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}
