import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { ListSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';

const RecordRow = ({ r, setView }) => (
  <button
    onClick={() => setView?.({ name: 'records', params: {} })}
    className="flex items-center justify-between py-2.5 gap-2 w-full text-left hover:bg-slate-800/20 rounded px-1 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
  >
    <div className="flex flex-col min-w-0">
      <span className="text-xs font-mono text-slate-300">#{r.row_index} · {r.scope_category}</span>
      <span className="text-[10px] text-slate-500">
        {r.co2e_tonnes != null ? `${Number(r.co2e_tonnes).toLocaleString(undefined, { maximumFractionDigits: 2 })} tCO₂e` : 'Unresolved'}
      </span>
    </div>
    <span className="text-slate-600">→</span>
  </button>
);

export const PendingApprovalsWidget = ({ filters, setView }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-summary', filters],
    () => apiService.getMetricsSummary(filters),
  );
  return (
    <WidgetFrame title="Pending Approvals" subtitle="Awaiting analyst review" status={status} onRetry={refetch}>
      <div className="flex flex-col items-start gap-2 h-full justify-center">
        <span className="text-4xl font-black text-warning-400 font-sans">{data?.pending_approval ?? 0}</span>
        <span className="text-xs text-slate-500">records need review before they can be audit-locked.</span>
        <button onClick={() => setView?.({ name: 'records', params: { status: 'SUSPICIOUS' } })}
          className="mt-1 text-[11px] font-semibold text-brand-400 hover:text-brand-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded">Open audit queue →</button>
      </div>
    </WidgetFrame>
  );
};

export const AuditQueueWidget = ({ setView }) => {
  const { status, data, refetch } = useWidgetData(
    ['audit-queue'],
    () => apiService.getRecords({ status: 'SUSPICIOUS' }),
    { isEmpty: (d) => !d || d.count === 0 },
  );
  const items = (data?.items || []).slice(0, 6);
  return (
    <WidgetFrame
      title="Audit Queue"
      subtitle="Suspicious records"
      status={status}
      onRetry={refetch}
      skeleton={<ListSkeleton rows={5} />}
      empty={<EmptyState title="Queue clear" message="No suspicious records to review." />}
    >
      <div className="flex flex-col divide-y divide-slate-800/50">
        {items.map((r) => <RecordRow key={r.id} r={r} setView={setView} />)}
      </div>
    </WidgetFrame>
  );
};

export const LockedRecordsWidget = ({ setView }) => {
  const { status, data, refetch } = useWidgetData(
    ['locked-records'],
    () => apiService.getRecords({ status: 'APPROVED' }),
  );
  return (
    <WidgetFrame title="Locked Records" subtitle="Audit-secured" status={status} onRetry={refetch}>
      <div className="flex flex-col items-start gap-2 h-full justify-center">
        <span className="text-4xl font-black text-success-400 font-sans">{data?.count ?? 0}</span>
        <span className="text-xs text-slate-500">approved records sealed on the immutable ledger.</span>
        <button onClick={() => setView?.({ name: 'records', params: { status: 'APPROVED' } })}
          className="mt-1 text-[11px] font-semibold text-brand-400 hover:text-brand-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded">View locked records →</button>
      </div>
    </WidgetFrame>
  );
};
