import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { KpiCard } from '../KpiCard';
import { KpiSkeleton, ChartSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';
import { ErrorState } from '../../ui/ErrorState';
import { TrendChart, DonutChart, scopeColor } from '../../charts';

const num = (v, d = 1) =>
  Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d });

const SCOPE_LABEL = { SCOPE_1: 'Scope 1', SCOPE_2: 'Scope 2', SCOPE_3: 'Scope 3' };

// --- KPI summary row (full width) ---
export const KpiSummaryWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-summary', filters],
    () => apiService.getMetricsSummary(filters),
  );

  if (status === 'loading') {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5">
            <KpiSkeleton />
          </div>
        ))}
      </div>
    );
  }
  if (status === 'error') {
    return <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl p-5"><ErrorState onRetry={refetch} /></div>;
  }

  const coveragePct = Math.round((data.coverage ?? 1) * 100);
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <KpiCard
        label="Total Emissions"
        value={num(data.total_co2e_tonnes)}
        unit="tCO₂e"
        accent="text-white"
        current={data.total_co2e_tonnes}
        previous={data.previous_total_co2e_tonnes}
        sub="vs previous period"
      />
      <KpiCard label="Data Coverage" value={`${coveragePct}%`} accent="text-emerald-400"
        sub={`${data.calculated_count} calculated · ${data.unresolved_count} unresolved`} />
      <KpiCard label="Pending Approval" value={num(data.pending_approval, 0)} accent="text-amber-400"
        sub="records awaiting review" />
      <KpiCard label="Ingestion Batches" value={num(data.batch_count, 0)} accent="text-brand-400"
        sub="files processed" />
    </div>
  );
};

// --- Emissions over time ---
export const EmissionsTrendWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-timeseries', filters],
    () => apiService.getMetricsTimeseries({ ...filters, bucket: 'month' }),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  const series = (data || []).map((r) => ({
    period: String(r.period).slice(0, 7),
    value: Number(r.co2e_tonnes),
  }));
  return (
    <WidgetFrame
      title="Emissions Over Time"
      subtitle="tCO₂e per month"
      status={status}
      onRetry={refetch}
      skeleton={<ChartSkeleton height={240} />}
      empty={<EmptyState title="No emissions yet" message="Upload and calculate data to see the trend." />}
    >
      <TrendChart data={series} xKey="period" valueKey="value" height={240} formatValue={(v) => num(v)} />
    </WidgetFrame>
  );
};

// --- Reports / export (Viewer) ---
export const ReportsWidget = () => (
  <WidgetFrame title="Reports" subtitle="Export" status="success">
    <div className="flex flex-col gap-3 h-full justify-center">
      <p className="text-xs text-slate-400 leading-relaxed">
        Download the emissions ledger (with CO₂e + provenance) as CSV for external reporting.
      </p>
      <button
        onClick={() => apiService.exportRecords({})}
        className="px-4 py-2.5 border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-200 hover:text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus:outline-none flex items-center justify-center gap-2"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
        Export CSV
      </button>
    </div>
  </WidgetFrame>
);

// --- Scope breakdown donut ---
export const ScopeBreakdownWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-breakdown-scope', filters],
    () => apiService.getMetricsBreakdown({ ...filters, dimension: 'scope' }),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  const slices = (data || []).map((r) => ({
    label: SCOPE_LABEL[r.key] || r.key,
    rawKey: r.key,
    value: Number(r.co2e_tonnes),
  }));
  return (
    <WidgetFrame
      title="Emissions by Scope"
      subtitle="GHG Protocol scopes"
      status={status}
      onRetry={refetch}
      skeleton={<ChartSkeleton height={240} />}
      empty={<EmptyState title="No emissions yet" />}
    >
      <DonutChart data={slices} height={240} formatValue={(v) => `${num(v)} tCO₂e`}
        colorFor={(label) => scopeColor(Object.keys(SCOPE_LABEL).find((k) => SCOPE_LABEL[k] === label) || label)} />
    </WidgetFrame>
  );
};
