
export const EmptyState = ({ title = 'No data yet', message, action }) => (
  <div className="h-full min-h-[120px] flex flex-col items-center justify-center text-center gap-2 py-6">
    <div className="p-2.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-slate-500">
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
      </svg>
    </div>
    <span className="text-xs font-semibold text-slate-300">{title}</span>
    {message && <span className="text-[11px] text-slate-500 max-w-[220px] leading-relaxed">{message}</span>}
    {action}
  </div>
);

export default EmptyState;
