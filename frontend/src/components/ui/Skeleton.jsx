
export const Skeleton = ({ className = '' }) => (
  <div className={`animate-pulse rounded-lg bg-slate-800/60 ${className}`} />
);

export const KpiSkeleton = () => (
  <div className="flex flex-col gap-3">
    <Skeleton className="h-3 w-20" />
    <Skeleton className="h-8 w-28" />
    <Skeleton className="h-3 w-16" />
  </div>
);

export const ChartSkeleton = ({ height = 240 }) => (
  <div className="flex flex-col gap-3">
    <Skeleton className="w-full" style={{ height }} />
  </div>
);

export const ListSkeleton = ({ rows = 4 }) => (
  <div className="flex flex-col gap-2.5">
    {Array.from({ length: rows }).map((_, i) => (
      <Skeleton key={i} className="h-8 w-full" />
    ))}
  </div>
);

export default Skeleton;
