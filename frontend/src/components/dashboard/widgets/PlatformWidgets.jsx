import { apiService } from '../../../services/api';
import { useWidgetData } from '../useWidgetData';
import { WidgetFrame } from '../WidgetFrame';
import { ListSkeleton } from '../../ui/Skeleton';
import { EmptyState } from '../../ui/EmptyState';

const num = (v, d = 1) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: d });

const usePlatform = () =>
  useWidgetData(['platform-metrics'], () => apiService.getPlatformMetrics());

export const CrossTenantWidget = () => {
  const { status, data, refetch } = usePlatform();
  const t = data?.totals;
  return (
    <WidgetFrame title="Cross-Tenant Overview" subtitle="All organizations" status={status} onRetry={refetch}>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Total emissions</span>
          <span className="text-xl font-black text-white">{num(t?.total_co2e_tonnes)} <span className="text-xs font-normal text-slate-500">tCO₂e</span></span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Organizations</span>
          <span className="text-xl font-black text-brand-400">{num(t?.organizations, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Records</span>
          <span className="text-xl font-black text-white">{num(t?.records, 0)}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Calculations</span>
          <span className="text-xl font-black text-white">{num(t?.current_calculations, 0)}</span>
        </div>
      </div>
    </WidgetFrame>
  );
};

export const SystemHealthWidget = () => {
  const { status, data, refetch } = usePlatform();
  return (
    <WidgetFrame title="System Health" subtitle="Platform status" status={status} onRetry={refetch}>
      <div className="flex flex-col gap-3 h-full justify-center">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-success-500 shadow-[0_0_8px_#10b981]" />
          <span className="text-sm font-bold text-success-400">Operational</span>
        </div>
        <div className="flex flex-col gap-1 text-[11px] font-mono text-slate-500">
          <span>Active factor datasets: {data?.totals?.active_datasets ?? 0}</span>
          <span>Organizations: {data?.totals?.organizations ?? 0}</span>
          <span>Records tracked: {num(data?.totals?.records, 0)}</span>
        </div>
      </div>
    </WidgetFrame>
  );
};

const usePlatformOrganizations = () => {
  const { status, data, refetch } = usePlatform();
  const isEmpty = status === 'success' && (!data?.organizations || data.organizations.length === 0);
  return { status: isEmpty ? 'empty' : status, data, refetch };
};

export const ActiveOrganizationsWidget = () => {
  const { status, data, refetch } = usePlatformOrganizations();
  return (
    <WidgetFrame
      title="Active Organizations"
      subtitle="By emissions"
      status={status}
      onRetry={refetch}
      skeleton={<ListSkeleton rows={5} />}
      empty={<EmptyState title="No organizations yet" message="Active organizations will appear here." />}
    >
      <div className="flex flex-col divide-y divide-slate-800/50">
        {(data?.organizations || []).slice(0, 7).map((o) => (
          <div key={o.id} className="flex items-center justify-between py-2 gap-2">
            <span className="text-xs font-medium text-slate-200 truncate">{o.name}</span>
            <span className="text-[11px] font-mono text-slate-400">{num(o.co2e_tonnes)} tCO₂e</span>
          </div>
        ))}
      </div>
    </WidgetFrame>
  );
};

export const DatasetInventoryWidget = () => {
  const { status, data, refetch } = useWidgetData(
    ['dataset-inventory'],
    () => apiService.getFactorDatasets(),
    { isEmpty: (d) => !d || d.length === 0 },
  );
  return (
    <WidgetFrame
      title="Dataset Inventory"
      subtitle="Emission factors"
      status={status}
      onRetry={refetch}
      skeleton={<ListSkeleton rows={5} />}
      empty={<EmptyState title="No datasets" message="Import a factor dataset." />}
    >
      <div className="flex flex-col divide-y divide-slate-800/50">
        {(data || []).slice(0, 7).map((d) => (
          <div key={d.id} className="flex items-center justify-between py-2 gap-2">
            <span className="text-xs font-medium text-slate-200">{d.publisher} v{d.version}</span>
            <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border ${
              d.status === 'ACTIVE' ? 'bg-success-950/30 border-success-500/20 text-success-400'
                : 'bg-slate-900 border-slate-800 text-slate-500'
            }`}>{d.status}</span>
          </div>
        ))}
      </div>
    </WidgetFrame>
  );
};
