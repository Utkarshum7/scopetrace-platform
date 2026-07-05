import { ListSkeleton } from '../ui/Skeleton';
import { EmptyState } from '../ui/EmptyState';
import { ErrorState } from '../ui/ErrorState';

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
  <div
    className={`bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4 h-full transition-all duration-300 ${className}`}
  >
    {(title || actions) && (
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          {title && <h3 className="text-sm font-bold text-white tracking-tight font-sans">{title}</h3>}
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
  </div>
);

export default WidgetFrame;
