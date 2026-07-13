import { ListSkeleton } from '../ui/Skeleton';
import { EmptyState } from '../ui/EmptyState';
import { ErrorState } from '../ui/ErrorState';
import { Card } from '../ui/Card';

/**
 * Shared widget shell. Maps a widget's status to the four canonical states.
 * DashboardPage/widgets never re-implement loading/error/empty handling.
 */
export const WidgetFrame = ({
  title,
  subtitle,
  status,
  onRetry,
  actions,
  skeleton,
  empty,
  children,
  className = '',
}) => (
  <Card className={`p-5 flex flex-col gap-4 h-full ${className}`}>
    {(title || actions) && (
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          {/* Phase 8 (8e): h2, not h3 -- every widget on the dashboard sits
              directly under the page's own h1 (PageHeader) with no h2 in
              between, so h3 here was a heading-level skip repeated across
              every widget on the page. h2 is the correct first sub-level. */}
          {title && <h2 className="text-sm font-bold text-white tracking-tight font-sans">{title}</h2>}
          {subtitle && <span className="text-[10px] text-slate-500 uppercase tracking-wider">{subtitle}</span>}
        </div>
        {actions}
      </div>
    )}
    <div className="flex-1 min-h-0">
      {status === 'loading' && (skeleton || <ListSkeleton />)}
      {status === 'error' && <ErrorState onRetry={onRetry} />}
      {status === 'empty' && (empty || <EmptyState />)}
      {status === 'success' && children}
    </div>
  </Card>
);

export default WidgetFrame;
