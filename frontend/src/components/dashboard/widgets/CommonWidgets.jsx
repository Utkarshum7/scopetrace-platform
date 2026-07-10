import { useState } from 'react';
import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { KpiCard } from '../KpiCard';
import { KpiSkeleton, ChartSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';
import { ErrorState } from '../../ui/ErrorState';
import { ConfidenceBadge } from '../../ui/ConfidenceBadge';
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

// --- Reports / export (Viewer, Auditor, Org Admin) ---
// Phase 7f extends this with an AI narrative sub-section -- read-only,
// fetched for the SAME period/scope filters the rest of the dashboard
// uses. Narration access is gated server-side to Org Admin/Auditor
// (CanViewActivity, matching the compliance report it narrates); for a
// Viewer, the fetch 403s and this section simply never renders -- no
// error banner, no redesign of the CSV export above it.
export const ReportsWidget = ({ filters = {} }) => {
  const hasRange = Boolean(filters.date_from && filters.date_to);
  const { status, data, refetch } = useWidgetData(
    ['report-narrations', filters],
    () => apiService.listReportNarrations(filters),
    { isEmpty: (d) => !d || d.length === 0, enabled: hasRange },
  );
  const [isRegenerating, setIsRegenerating] = useState(false);
  const latest = status === 'success' && data && data.length > 0 ? data[0] : null;

  const handleRegenerate = async () => {
    setIsRegenerating(true);
    try {
      await apiService.regenerateReportNarration(filters);
      // The narration is generated asynchronously on the AI queue -- a
      // single refetch right after queuing won't show it yet, but keeps
      // the widget honest about "queued, not instant" rather than
      // implying a synchronous result.
      refetch();
    } finally {
      setIsRegenerating(false);
    }
  };

  return (
    <WidgetFrame title="Reports" subtitle="Export" status="success">
      <div className="flex flex-col gap-3 h-full justify-center">
        <p className="text-xs text-slate-400 leading-relaxed">
          Download the emissions ledger (with CO₂e + provenance) as CSV for external reporting.
        </p>
        <button
          onClick={() => apiService.exportRecords({})}
          className="px-4 py-2.5 border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-200 hover:text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          Export CSV
        </button>

        {hasRange && status !== 'error' && (
          <div className="flex flex-col gap-2 pt-3 border-t border-slate-800/60">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-wider">
                AI Narrative
                <span className="px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-400/30 text-indigo-200 text-[9px] tracking-wide">
                  AI Advisory
                </span>
              </span>
              <button
                onClick={handleRegenerate}
                disabled={isRegenerating}
                aria-label={isRegenerating ? 'Queuing report narration regeneration' : 'Regenerate AI narrative'}
                className="text-[10px] text-brand-400 hover:text-brand-300 font-bold uppercase tracking-wider transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded disabled:opacity-40"
              >
                {isRegenerating ? 'Queuing…' : 'Regenerate'}
              </button>
            </div>

            {status === 'loading' && <p className="text-[10px] text-slate-500">Loading…</p>}

            {latest && (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Executive summary
                  </span>
                  <ConfidenceBadge confidence={latest.confidence} />
                </div>
                <p className="text-[11px] text-slate-300 leading-relaxed">{latest.executive_summary}</p>

                {latest.key_highlights?.length > 0 && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                      Key highlights
                    </span>
                    <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                      {latest.key_highlights.map((h, i) => (
                        <li key={i}>{h}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {latest.trend_explanations && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                      Trend
                    </span>
                    <p className="text-[10px] text-indigo-200/90 leading-relaxed">{latest.trend_explanations}</p>
                  </div>
                )}

                {latest.recommendations?.length > 0 && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                      Advisory recommendations
                    </span>
                    <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                      {latest.recommendations.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {status === 'empty' && (
              <p className="text-[10px] text-slate-500 italic">
                No narrative yet for this period — click Regenerate.
              </p>
            )}
          </div>
        )}
      </div>
    </WidgetFrame>
  );
};

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
