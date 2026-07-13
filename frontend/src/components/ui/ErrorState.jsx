
export const ErrorState = ({ message = 'Could not load this widget.', onRetry }) => (
  <div className="h-full min-h-[120px] flex flex-col items-center justify-center text-center gap-2 py-6">
    <div className="p-2.5 rounded-xl bg-danger-950/30 border border-danger-500/30 text-danger-400">
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    </div>
    <span className="text-xs font-semibold text-danger-300">{message}</span>
    {onRetry && (
      <button
        onClick={onRetry}
        className="mt-1 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800 text-[11px] font-semibold uppercase tracking-wider transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        Retry
      </button>
    )}
  </div>
);

export default ErrorState;
