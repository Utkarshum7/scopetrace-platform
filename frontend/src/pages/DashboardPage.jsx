import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { widgetsForRole, SPAN_CLASS } from '../components/dashboard/registry';
import { WidgetErrorBoundary } from '../components/dashboard/WidgetErrorBoundary';
import { DashboardFilters, periodToFilters } from '../components/dashboard/DashboardFilters';

/**
 * DashboardPage orchestrates LAYOUT only: it resolves the role's widget set from
 * the registry, applies the shared period/scope filters, and lays out the grid.
 * It holds no data-fetching or widget-specific logic — each widget is
 * self-contained and isolated by an error boundary.
 */
export const DashboardPage = ({ setView }) => {
  const { role, isPlatformAdmin, user } = useAuth();
  const [control, setControl] = useState({ period: '12m', scope: '' });
  const filters = periodToFilters(control.period, control.scope);
  const widgets = widgetsForRole(role, isPlatformAdmin);
  const orgName = user?.active_organization?.name || (isPlatformAdmin ? 'All Organizations' : '');

  return (
    <div className="flex flex-col gap-6 animate-fadeIn">
      {/* Header + filters */}
      <div className="flex flex-wrap justify-between items-center gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-black text-white tracking-tight font-sans">
            ESG Command Dashboard
          </h1>
          <p className="text-xs text-slate-400">
            {orgName ? `${orgName} · ` : ''}carbon accounting overview
          </p>
        </div>
        <DashboardFilters period={control.period} scope={control.scope} onChange={setControl} />
      </div>

      {/* Role-aware, pluggable widget grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-6 gap-4">
        {widgets.map(({ id, component: Widget, span }) => (
          <div key={id} className={SPAN_CLASS[span]}>
            <WidgetErrorBoundary>
              <Widget filters={filters} setView={setView} />
            </WidgetErrorBoundary>
          </div>
        ))}
      </div>
    </div>
  );
};

export default DashboardPage;
