const PERIODS = [
  { key: '3m', label: '3M', months: 3 },
  { key: '6m', label: '6M', months: 6 },
  { key: '12m', label: '12M', months: 12 },
  { key: 'all', label: 'All', months: null },
];

const SCOPES = [
  { key: '', label: 'All scopes' },
  { key: 'SCOPE_1', label: 'Scope 1' },
  { key: 'SCOPE_2', label: 'Scope 2' },
  { key: 'SCOPE_3', label: 'Scope 3' },
];

// Derive an ISO date_from from a period preset.
export function periodToFilters(periodKey, scope) {
  const preset = PERIODS.find((p) => p.key === periodKey) || PERIODS[2];
  const filters = {};
  if (preset.months) {
    const d = new Date();
    d.setMonth(d.getMonth() - preset.months);
    filters.date_from = d.toISOString().slice(0, 10);
    filters.date_to = new Date().toISOString().slice(0, 10);
  }
  if (scope) filters.scope = scope;
  return filters;
}

export const DashboardFilters = ({ period, scope, onChange }) => (
  <div className="flex items-center gap-3 flex-wrap">
    <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1">
      {PERIODS.map((p) => (
        <button
          key={p.key}
          onClick={() => onChange({ period: p.key, scope })}
          className={`px-3 py-1 rounded-md text-[11px] font-bold uppercase tracking-wider transition-all focus:outline-none ${
            period === p.key ? 'bg-brand-500/15 text-brand-300' : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
    <select
      value={scope}
      onChange={(e) => onChange({ period, scope: e.target.value })}
      className="bg-slate-900 border border-slate-800 rounded-lg py-1.5 px-3 text-xs text-slate-300 focus:outline-none focus:ring-2 focus:ring-brand-500 cursor-pointer"
    >
      {SCOPES.map((s) => (
        <option key={s.key} value={s.key}>{s.label}</option>
      ))}
    </select>
  </div>
);

export default DashboardFilters;
