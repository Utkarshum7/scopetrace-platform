
/**
 * ▲/▼ delta chip comparing current vs previous. Green for down (emissions
 * falling is good), rose for up. Renders nothing when previous is unavailable.
 */
export const Trend = ({ current, previous }) => {
  const cur = Number(current);
  const prev = Number(previous);
  if (previous == null || Number.isNaN(prev) || prev === 0 || Number.isNaN(cur)) {
    return null;
  }
  const deltaPct = ((cur - prev) / prev) * 100;
  const up = deltaPct > 0;
  const flat = Math.abs(deltaPct) < 0.05;
  const color = flat ? 'text-slate-400' : up ? 'text-rose-400' : 'text-emerald-400';
  const arrow = flat ? '→' : up ? '▲' : '▼';
  const direction = flat ? 'Unchanged' : up ? 'Increased' : 'Decreased';
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-bold ${color}`}
      aria-label={`${direction} ${Math.abs(deltaPct).toFixed(1)}% versus previous period`}
    >
      <span aria-hidden="true">{arrow} {Math.abs(deltaPct).toFixed(1)}%</span>
    </span>
  );
};

export default Trend;
