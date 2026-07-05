// Design-token palette for all charts. Library-agnostic — referenced by the
// chart wrapper components only. Swapping chart libraries reuses these tokens.
export const chartColors = {
  brand: '#2ebb72',
  brandLight: '#53ce91',
  grid: '#1e293b', // slate-800
  axis: '#64748b', // slate-500
  tooltipBg: '#0b0f19',
  tooltipBorder: '#334155',
  scope: {
    SCOPE_1: '#2ebb72',
    SCOPE_2: '#38bdf8',
    SCOPE_3: '#f59e0b',
  },
  palette: ['#2ebb72', '#38bdf8', '#f59e0b', '#a78bfa', '#f472b6', '#facc15'],
};

export const axisProps = {
  stroke: chartColors.axis,
  fontSize: 11,
  tickLine: false,
  axisLine: { stroke: chartColors.grid },
};

export const tooltipStyle = {
  backgroundColor: chartColors.tooltipBg,
  border: `1px solid ${chartColors.tooltipBorder}`,
  borderRadius: 8,
  fontSize: 12,
  color: '#e2e8f0',
};

export const scopeColor = (label) =>
  chartColors.scope[label] || chartColors.palette[0];
