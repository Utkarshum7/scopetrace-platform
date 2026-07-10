import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { ListSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';

const num = (v, d = 1) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d });
const money = (v) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const OrgKpisWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-summary', filters],
    () => apiService.getMetricsSummary(filters),
  );
  return (
    <WidgetFrame title="Organization Totals" subtitle="This tenant" status={status} onRetry={refetch}>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Total emissions</span>
          <span className="text-xl font-black text-white">{num(data?.total_co2e_tonnes)} <span className="text-xs font-normal text-slate-500">tCO₂e</span></span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Calculations</span>
          <span className="text-xl font-black text-white">{num(data?.calculated_count, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Batches</span>
          <span className="text-xl font-black text-brand-400">{num(data?.batch_count, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Pending</span>
          <span className="text-xl font-black text-amber-400">{num(data?.pending_approval, 0)}</span>
        </div>
      </div>
    </WidgetFrame>
  );
};

export const CoverageWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['metrics-summary', filters],
    () => apiService.getMetricsSummary(filters),
  );
  const pct = Math.round((data?.coverage ?? 1) * 100);
  return (
    <WidgetFrame title="Data Coverage" subtitle="Calculated vs unresolved" status={status} onRetry={refetch}>
      <div className="flex flex-col gap-3 h-full justify-center">
        <div className="flex items-baseline gap-2">
          <span className="text-3xl font-black text-emerald-400">{pct}%</span>
          <span className="text-xs text-slate-500">of records have CO₂e</span>
        </div>
        <div className="w-full bg-slate-800 rounded-full h-2.5">
          <div className="bg-emerald-500 h-2.5 rounded-full transition-all duration-500 shadow-[0_0_8px_#10b981]" style={{ width: `${pct}%` }} />
        </div>
        <span className="text-[10px] text-slate-500">{data?.calculated_count ?? 0} calculated · {data?.unresolved_count ?? 0} unresolved</span>
      </div>
    </WidgetFrame>
  );
};

export const UserActivityWidget = () => {
  const { status, data, refetch } = useWidgetData(
    ['activity-feed'],
    () => apiService.getActivityFeed(),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  const entries = (data || []).slice(0, 7);
  return (
    <WidgetFrame
      title="User Activity"
      subtitle="Audit trail"
      status={status}
      onRetry={refetch}
      skeleton={<ListSkeleton rows={6} />}
      empty={<EmptyState title="No activity yet" message="Approvals and changes will appear here." />}
    >
      <div className="flex flex-col divide-y divide-slate-800/50">
        {entries.map((e, i) => (
          <div key={i} className="flex items-center justify-between py-2 gap-2">
            <div className="flex flex-col min-w-0">
              <span className="text-xs text-slate-300 truncate">{e.action.replace(/_/g, ' ')}</span>
              <span className="text-[10px] text-slate-500">{e.changed_by || 'system'}</span>
            </div>
            <span className="text-[10px] text-slate-500 shrink-0">{new Date(e.timestamp).toLocaleDateString()}</span>
          </div>
        ))}
      </div>
    </WidgetFrame>
  );
};

// Phase 7g -- org-scoped AI cost governance. CanViewAICosts (Org Admin /
// Auditor / Platform Admin) is the real boundary, apps.ai.ops_views.
// AICostGovernanceView -- placed alongside the other Org Admin widgets
// since that's the primary audience; also registered for Auditor (see
// registry.js) to match the permission class's own role set.
export const AIBudgetWidget = ({ filters }) => {
  const { status, data, refetch } = useWidgetData(
    ['ai-costs', filters],
    () => apiService.getAICosts(filters),
  );
  const pct = data?.budget?.utilization_pct;
  const barPct = Math.min(pct ?? 0, 100);
  return (
    <WidgetFrame title="AI Budget" subtitle="This month" status={status} onRetry={refetch}>
      {!data?.ai_enabled ? (
        <EmptyState title="AI is not enabled for this organization." />
      ) : (
        <div className="flex flex-col gap-3 h-full justify-center">
          <div className="flex items-baseline gap-2">
            <span className={`text-3xl font-black ${data?.budget?.over_budget ? 'text-rose-400' : 'text-emerald-400'}`}>
              {pct != null ? `${pct}%` : '—'}
            </span>
            <span className="text-xs text-slate-500">of monthly budget used</span>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-2.5">
            <div
              className={`h-2.5 rounded-full transition-all duration-500 ${data?.budget?.over_budget ? 'bg-rose-500 shadow-[0_0_8px_#f43f5e]' : 'bg-emerald-500 shadow-[0_0_8px_#10b981]'}`}
              style={{ width: `${barPct}%` }}
            />
          </div>
          <span className="text-[10px] text-slate-500">
            ${money(data?.budget?.spent_usd)} spent of ${money(data?.budget?.budget_usd)} · {num(data?.token_consumption?.input_tokens, 0)} in / {num(data?.token_consumption?.output_tokens, 0)} out tokens
          </span>
        </div>
      )}
    </WidgetFrame>
  );
};

export const FactorDatasetWidget = () => {
  const { status, data, refetch } = useWidgetData(
    ['active-factor-datasets'],
    () => apiService.getFactorDatasets({ status: 'ACTIVE' }),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  const ds = (data || [])[0];
  return (
    <WidgetFrame
      title="Emission Factors"
      subtitle="Active dataset"
      status={status}
      onRetry={refetch}
      empty={<EmptyState title="No active dataset" message="Import a factor dataset to enable CO₂e." />}
    >
      {ds && (
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-black text-white">{ds.publisher}</span>
            <span className="text-sm font-mono text-brand-400">v{ds.version}</span>
          </div>
          <div className="flex flex-col gap-1 text-[11px] font-mono text-slate-500">
            <span>Region: {ds.region_code || 'GLOBAL'}</span>
            <span>Valid: {ds.valid_from} → {ds.valid_to || '—'}</span>
            <span className="truncate">Source: {ds.source_filename || '—'}</span>
          </div>
        </div>
      )}
    </WidgetFrame>
  );
};
