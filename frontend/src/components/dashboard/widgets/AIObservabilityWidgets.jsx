// Phase 7g -- Platform Admin AI observability widgets. All four share one
// query (apiService.getAIObservability), matching PlatformWidgets.jsx's own
// usePlatform() precedent -- TanStack Query dedups the shared key into one
// request regardless of how many widgets mount. Server-side RBAC
// (IsPlatformAdmin, apps.ai.ops_views.AIObservabilityView) is the real
// boundary; these widgets are only reachable from the PLATFORM_ADMIN
// registry section (see registry.js), never rendered for a lower role.
import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { TrendChart, DonutChart } from '../../charts';

const num = (v, d = 1) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d });

const useAIObservability = (filters) =>
  useWidgetData(['ai-observability', filters], () => apiService.getAIObservability(filters));

export const AIUsageWidget = ({ filters }) => {
  const { status, data, refetch } = useAIObservability(filters);
  return (
    <WidgetFrame title="AI Usage" subtitle="Gateway requests" status={status} onRetry={refetch}>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Total requests</span>
          <span className="text-xl font-black text-white">{num(data?.requests?.total, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Failed</span>
          <span className="text-xl font-black text-rose-400">{num(data?.requests?.failed, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Cache hits</span>
          <span className="text-xl font-black text-brand-400">{num(data?.cache_hits, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Replay calls</span>
          <span className="text-xl font-black text-white">{num(data?.replay_usage, 0)}</span>
        </div>
      </div>
    </WidgetFrame>
  );
};

export const AIProviderMixWidget = ({ filters }) => {
  const { status, data, refetch } = useAIObservability(filters);
  const chartData = Object.entries(data?.provider_usage || {}).map(([label, value]) => ({ label, value }));
  return (
    <WidgetFrame title="Provider Mix" subtitle="Requests by provider" status={status} onRetry={refetch}>
      {chartData.length > 0 ? (
        <DonutChart data={chartData} height={220} formatValue={(v) => num(v, 0)} />
      ) : (
        <div className="text-xs text-slate-500 flex items-center justify-center h-full">No AI requests yet.</div>
      )}
    </WidgetFrame>
  );
};

export const AIEvaluationWidget = ({ filters }) => {
  const { status, data, refetch } = useAIObservability(filters);
  const evaluation = data?.evaluation;
  return (
    <WidgetFrame title="Evaluation Health" subtitle="Golden dataset suite" status={status} onRetry={refetch}>
      <div className="flex flex-col gap-3 h-full">
        <div className="grid grid-cols-3 gap-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Regressions</span>
            <span className="text-lg font-black text-rose-400">{num(evaluation?.regressions, 0)}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Schema fails</span>
            <span className="text-lg font-black text-amber-400">{num(evaluation?.schema_failures, 0)}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Replay fails</span>
            <span className="text-lg font-black text-amber-400">{num(evaluation?.replay_failures, 0)}</span>
          </div>
        </div>
        <div className="flex flex-col gap-1.5 pt-1 border-t border-slate-800/50">
          {Object.entries(evaluation?.latest_by_tier || {}).map(([tier, run]) => (
            <div key={tier} className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-slate-400 truncate">{tier.replace(/_/g, ' ')}</span>
              {run ? (
                <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border ${
                  run.failed_cases === 0 ? 'bg-emerald-950/30 border-emerald-500/20 text-emerald-400'
                    : 'bg-rose-950/30 border-rose-500/20 text-rose-400'
                }`}>{run.passed_cases}/{run.total_cases} passed</span>
              ) : (
                <span className="text-[10px] text-slate-600">never run</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </WidgetFrame>
  );
};

export const AILatencyTrendWidget = ({ filters }) => {
  const { status, data, refetch } = useAIObservability(filters);
  const trend = data?.latency?.trend || [];
  return (
    <WidgetFrame
      title="Latency Trend"
      subtitle={`Avg ${num(data?.latency?.avg_ms)} ms overall`}
      status={status}
      onRetry={refetch}
    >
      {trend.length > 0 ? (
        <TrendChart data={trend} xKey="date" valueKey="avg_ms" height={220} formatValue={(v) => `${num(v, 0)}ms`} />
      ) : (
        <div className="text-xs text-slate-500 flex items-center justify-center h-full">No latency data yet.</div>
      )}
    </WidgetFrame>
  );
};
