import { Skeleton } from "@/components/ui/skeleton";

export default function SessionsLoading() {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Skeleton className="h-8 w-36" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>

      <div className="flex gap-4">
        <aside className="bg-card hidden w-[240px] shrink-0 rounded-xl border p-3 lg:block">
          <div className="space-y-3">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-8 w-full" />
          </div>
        </aside>

        <div className="min-w-0 flex-1 space-y-3">
          <div className="bg-card rounded-xl border p-3">
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, rowIndex) => (
                <div key={rowIndex} className="grid grid-cols-5 gap-3">
                  {Array.from({ length: 5 }).map((__, colIndex) => (
                    <Skeleton key={`${rowIndex}-${colIndex}`} className="h-5 w-full" />
                  ))}
                </div>
              ))}
            </div>
          </div>

          <div className="bg-card rounded-xl border p-3">
            <Skeleton className="h-8 w-80 max-w-full" />
          </div>
        </div>
      </div>
    </div>
  );
}
