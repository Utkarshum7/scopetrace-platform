/**
 * StatusBadge Component
 * Renders a highly visual premium pill badge showing the validation/approval state of records or batches.
 */
export const StatusBadge = ({ status }) => {
  const normStatus = (status || '').trim().toUpperCase();

  let styles = {
    bg: 'bg-slate-800/80 border-slate-700 text-slate-300',
    dot: 'bg-slate-400',
    label: normStatus || 'UNKNOWN',
  };

  switch (normStatus) {
    case 'DRAFT':
      styles = {
        bg: 'bg-blue-950/40 border-blue-500/30 text-blue-300',
        dot: 'bg-blue-400 shadow-[0_0_8px_#3b82f6]',
        label: 'Draft / Unverified',
      };
      break;
    case 'SUSPICIOUS':
      styles = {
        bg: 'bg-warning-950/40 border-warning-500/30 text-warning-300 animate-pulse',
        dot: 'bg-warning-400 shadow-[0_0_8px_#f59e0b]',
        label: 'Suspicious / Flagged',
      };
      break;
    case 'VALIDATED':
      styles = {
        bg: 'bg-sky-950/40 border-sky-500/30 text-sky-300',
        dot: 'bg-sky-400 shadow-[0_0_8px_#38bdf8]',
        label: 'Validated / Ready',
      };
      break;
    case 'SUBMITTED':
      styles = {
        bg: 'bg-violet-950/40 border-violet-500/30 text-violet-300',
        dot: 'bg-violet-400 shadow-[0_0_8px_#a78bfa]',
        label: 'Submitted for Approval',
      };
      break;
    case 'APPROVED':
      styles = {
        bg: 'bg-success-950/40 border-success-500/30 text-success-300',
        dot: 'bg-success-400 shadow-[0_0_8px_#10b981]',
        label: 'Approved & Locked',
      };
      break;
    case 'REJECTED':
      styles = {
        bg: 'bg-orange-950/40 border-orange-500/30 text-orange-300',
        dot: 'bg-orange-400 shadow-[0_0_8px_#fb923c]',
        label: 'Rejected / Needs Correction',
      };
      break;
    case 'FAILED':
      styles = {
        bg: 'bg-danger-950/40 border-danger-500/30 text-danger-300',
        dot: 'bg-danger-500 shadow-[0_0_8px_#ef4444]',
        label: 'Failed Ingest',
      };
      break;
    case 'COMPLETED':
      styles = {
        bg: 'bg-success-950/40 border-success-500/30 text-success-300',
        dot: 'bg-success-400 shadow-[0_0_8px_#10b981]',
        label: 'Completed',
      };
      break;
    case 'PROCESSING':
      styles = {
        bg: 'bg-indigo-950/40 border-indigo-500/30 text-indigo-300 animate-pulse',
        dot: 'bg-indigo-400 shadow-[0_0_8px_#6366f1]',
        label: 'Processing',
      };
      break;
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-full border ${styles.bg} transition-all duration-300`}
    >
      <span className={`w-2 h-2 rounded-full ${styles.dot}`} />
      <span className="tracking-wide">{styles.label}</span>
    </span>
  );
};

export default StatusBadge;
