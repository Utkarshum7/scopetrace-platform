import { Trend } from '../ui/Trend';
import { Card } from '../ui/Card';

export const KpiCard = ({ label, value, unit, sub, current, previous, accent = 'text-white' }) => (
  <Card className="p-5 flex flex-col gap-1.5 hover:border-brand-500/30">
    <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">{label}</span>
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className={`text-2xl font-black tracking-tight font-sans ${accent}`}>{value}</span>
      {unit && <span className="text-sm font-normal text-slate-500">{unit}</span>}
      {current != null && previous != null && <Trend current={current} previous={previous} />}
    </div>
    {sub && <span className="text-[10px] text-slate-500">{sub}</span>}
  </Card>
);

export default KpiCard;
