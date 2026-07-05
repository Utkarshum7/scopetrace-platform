import { Trend } from '../ui/Trend';

export const KpiCard = ({ label, value, unit, sub, current, previous, accent = 'text-white' }) => (
  <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-1.5 transition-all duration-300 hover:border-brand-500/30">
    <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">{label}</span>
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className={`text-2xl font-black tracking-tight font-sans ${accent}`}>{value}</span>
      {unit && <span className="text-sm font-normal text-slate-500">{unit}</span>}
      {current != null && previous != null && <Trend current={current} previous={previous} />}
    </div>
    {sub && <span className="text-[10px] text-slate-500">{sub}</span>}
  </div>
);

export default KpiCard;
