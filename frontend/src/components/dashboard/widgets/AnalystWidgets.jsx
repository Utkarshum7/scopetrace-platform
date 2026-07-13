import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { ListSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';
import { StatusBadge } from '../../StatusBadge';
import { BarChart } from '../../charts';

const num = (v, d = 0) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d });

export const UploadShortcutWidget = ({ setView }) => (
  <WidgetFrame title="Ingest Data" subtitle="Analyst shortcut" status="success">
    <div className="flex flex-col gap-3 h-full justify-center">
      <p className="text-xs text-slate-400 leading-relaxed">
        Upload SAP fuel, utility, or corporate travel exports to extract, validate, and calculate emissions.
      </p>
      <button
        onClick={() => setView?.({ name: 'upload', params: {} })}
        className="px-4 py-2.5 bg-brand-600 hover:bg-brand-500 text-white text-xs font-black uppercase tracking-wider rounded-lg transition-all shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 flex items-center justify-center gap-2"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        Open Upload Center
      </button>
    </div>
  </WidgetFrame>
);

export const RecentIngestionWidget = ({ setView }) => {
  const { status, data, refetch } = useWidgetData(
    ['recent-batches'],
    () => apiService.getBatches(),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  const batches = (data || []).slice(0, 6);
  return (
    <WidgetFrame
      title="Recent Ingestion"
      subtitle="Latest uploads"
      status={status}
      onRetry={refetch}
      skeleton={<ListSkeleton rows={5} />}
      empty={<EmptyState title="No uploads yet" message="Ingested files will appear here." />}
      actions={
        <button onClick={() => setView?.({ name: 'records', params: {} })}
          className="text-[11px] font-semibold text-brand-400 hover:text-brand-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded">Ledger →</button>
      }
    >
      <div className="flex flex-col divide-y divide-slate-800/50">
        {batches.map((b) => (
          <div key={b.id} className="flex items-center justify-between py-2.5 gap-2">
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-medium text-slate-200 truncate max-w-[160px]" title={b.file_name}>{b.file_name}</span>
              <span className="text-[10px] text-slate-500">{new Date(b.created_at).toLocaleDateString()} · {b.total_rows} rows</span>
            </div>
            <StatusBadge status={b.status} />
          </div>
        ))}
      </div>
    </WidgetFrame>
  );
};

export const ValidationSummaryWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-summary', filters],
    () => apiService.getMetricsSummary(filters),
  );
  const chart = data ? [
    { label: 'Calculated', value: data.calculated_count },
    { label: 'Unresolved', value: data.unresolved_count },
    { label: 'Pending', value: data.pending_approval },
  ] : [];
  return (
    <WidgetFrame title="Validation Summary" subtitle="Record health" status={status} onRetry={refetch}>
      <div className="flex flex-col gap-3">
        <BarChart
          data={chart}
          xKey="label"
          valueKey="value"
          height={180}
          formatValue={(v) => num(v)}
          ariaLabel={`Record validation status: ${chart.map((c) => `${c.label} ${num(c.value)}`).join(', ') || 'no data'}`}
        />
      </div>
    </WidgetFrame>
  );
};
