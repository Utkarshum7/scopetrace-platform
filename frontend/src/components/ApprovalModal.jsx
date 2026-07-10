import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiService } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { Modal } from './ui/Modal';
import { Spinner } from './ui/Spinner';

// Phase 6c governance workflow, mirrored from EmissionRecord.WORKFLOW_TRANSITIONS
// (backend/apps/ingestion/models.py) -- kept as a small local constant rather
// than fetched from /workflow/ so opening this modal is a single round trip.
const SUBMITTABLE_STATUSES = ['DRAFT', 'SUSPICIOUS', 'VALIDATED', 'REJECTED'];
const REVIEWABLE_STATUSES = ['SUBMITTED'];

/**
 * ApprovalModal Component
 * Workflow-aware: the record's CURRENT status decides which action(s) are
 * offered -- Submit (Draft/Suspicious/Validated/Rejected), or Approve/Reject
 * (Submitted). Reason is optional for submit/approve, required for reject.
 * Dialog mechanics (focus trap, initial focus, restoration, Escape-to-close)
 * live in the shared Modal wrapper (Phase 8, 8a.3) -- this component owns
 * only its own content and the reason textarea as the initial-focus target.
 */
export const ApprovalModal = ({ isOpen, record, onClose, onActionComplete }) => {
  const { canApprove } = useAuth();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const initialFocusRef = useRef(null);

  if (!isOpen || !record) return null;

  const isReviewable = REVIEWABLE_STATUSES.includes(record.status);
  const isSubmittable = SUBMITTABLE_STATUSES.includes(record.status);
  const isResubmit = record.status === 'REJECTED';

  // Phase 7.5 (H4-6): submit/approve/reject change server state (record
  // status, pending-approval counts, scope/KPI totals) that dashboard
  // widgets cache via TanStack Query (60s staleTime, see useWidgetData) --
  // without this, a widget on another page could show stale counts for up
  // to a minute after an action taken here. RecordsPage's own list refresh
  // (onActionComplete -> fetchRecords) is separate/unaffected; this only
  // covers the TanStack-Query-cached dashboard surface. A global
  // invalidateQueries() (no key filter) is the safe default here rather
  // than enumerating exact keys -- this app's query keys are flat strings,
  // not a hierarchy, so a broad invalidation risks a wasted refetch at
  // worst, never a missed one.
  const invalidateDashboardQueries = () => {
    queryClient.invalidateQueries();
  };

  const finish = () => {
    onActionComplete();
    invalidateDashboardQueries();
    setReason('');
    setErrorMsg(null);
    onClose();
  };

  const runAction = async (fn, failureMessage) => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      await fn();
      finish();
    } catch (error) {
      setErrorMsg(error.response?.data?.detail || failureMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = () => runAction(
    () => apiService.submitRecord(record.id, reason.trim()),
    'Submission failed.'
  );

  const handleApprove = () => runAction(
    () => apiService.approveRecord(record.id, reason.trim()),
    'Approval failed.'
  );

  const handleReject = () => runAction(
    () => apiService.rejectRecord(record.id, reason.trim()),
    'Rejection failed.'
  );

  // Convenience action: submit then approve. Each step still goes through
  // the backend's own transition validation -- if submit succeeds but the
  // approve step fails (e.g. a race, or the actor loses approve rights
  // mid-flow), the record is left correctly in SUBMITTED state and the
  // list is still refreshed to reflect that partial progress.
  const handleSubmitAndApprove = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      await apiService.submitRecord(record.id, reason.trim());
      try {
        await apiService.approveRecord(record.id, reason.trim());
      } catch (approveError) {
        onActionComplete();
        invalidateDashboardQueries();  // submit succeeded even though approve didn't
        setErrorMsg(
          approveError.response?.data?.detail ||
          'Record was submitted, but automatic approval failed. It is now awaiting approval.'
        );
        return;
      }
      finish();
    } catch (submitError) {
      setErrorMsg(submitError.response?.data?.detail || 'Submission failed.');
    } finally {
      setIsLoading(false);
    }
  };

  const reasonTrimmed = reason.trim().length > 0;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      titleId="approval-modal-title"
      initialFocusRef={initialFocusRef}
      className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl flex flex-col gap-4"
    >
      {/* Header */}
      <div className="flex justify-between items-start">
        <div className="flex flex-col gap-1">
          <h3 id="approval-modal-title" className="text-lg font-bold text-white font-sans tracking-tight">
            {isReviewable
              ? 'Review Submitted Record'
              : isResubmit
              ? 'Resubmit Record for Approval'
              : 'Submit Record for Approval'}
          </h3>
          <p className="text-xs text-slate-400">
            Row Index #{record.row_index} in batch
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close dialog"
          className="text-slate-400 hover:text-white p-1 hover:bg-slate-800/80 rounded transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <svg
            className="w-5 h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      {/* Record Snapshot Details */}
      <div className="bg-slate-950/50 rounded-lg p-3.5 border border-slate-800/60 flex flex-col gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-slate-500">Calculated Quantity:</span>
          <span className="font-semibold text-slate-200">
            {record.normalized_value ? parseFloat(record.normalized_value).toLocaleString() : 'N/A'} {record.normalized_unit}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Scope Classification:</span>
          <span className="font-semibold text-brand-400">{record.scope_category}</span>
        </div>
        {record.is_suspicious && (
          <div className="mt-1 flex items-center gap-1.5 p-2 bg-amber-950/20 border border-amber-500/20 rounded text-amber-300 text-[11px] font-medium leading-relaxed animate-pulse">
            <svg
              className="w-3.5 h-3.5 flex-shrink-0"
              fill="currentColor"
              viewBox="0 0 20 20"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                fillRule="evenodd"
                d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                clipRule="evenodd"
              />
            </svg>
            Anomalous threshold flag is active on this record.
          </div>
        )}
      </div>

      {/* Form */}
      <form onSubmit={(e) => e.preventDefault()} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="approval-reason" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            {isReviewable ? 'Review Rationale / Reason' : 'Submission Note (optional)'}
            {isReviewable && <span className="text-rose-400 normal-case"> — required to reject</span>}
          </label>
          <textarea
            id="approval-reason"
            ref={initialFocusRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Verified with supplier invoices; outlier due to seasonal Q1 production surge."
            rows={3}
            aria-required={isReviewable ? 'true' : 'false'}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all resize-none"
            disabled={isLoading}
          />
        </div>

        {errorMsg && (
          <div role="alert" className="p-3 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-xs rounded-lg flex items-center gap-2 animate-shake">
            <svg
              className="w-4 h-4 flex-shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
            <span className="font-medium">{errorMsg}</span>
          </div>
        )}

        <div className="flex justify-end gap-3 pt-2 flex-wrap">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 border border-slate-800 bg-slate-900 hover:bg-slate-800/80 text-slate-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500"
            disabled={isLoading}
          >
            Cancel
          </button>

          {isReviewable && (
            <button
              type="button"
              onClick={handleReject}
              disabled={isLoading || !reasonTrimmed}
              title={!reasonTrimmed ? 'A reason is required to reject a record.' : undefined}
              className="px-4 py-2 bg-rose-950/40 hover:bg-rose-900/50 disabled:bg-slate-800 disabled:text-slate-600 border border-rose-500/30 text-rose-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            >
              Reject
            </button>
          )}

          {isSubmittable && canApprove && (
            <button
              type="button"
              onClick={handleSubmitAndApprove}
              disabled={isLoading}
              className="px-4 py-2 bg-emerald-950/40 hover:bg-emerald-900/50 disabled:bg-slate-800 disabled:text-slate-600 border border-emerald-500/30 text-emerald-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
            >
              Submit & Approve
            </button>
          )}

          <button
            type="button"
            onClick={isReviewable ? handleApprove : handleSubmit}
            disabled={isLoading}
            className="px-5 py-2 bg-brand-600 hover:bg-brand-500 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all shadow-md shadow-brand-600/10 flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-900"
          >
            {isLoading && <Spinner className="h-3.5 w-3.5 text-white" />}
            {isReviewable ? 'Confirm & Lock' : isResubmit ? 'Resubmit' : 'Submit for Approval'}
          </button>
        </div>
      </form>
    </Modal>
  );
};

export default ApprovalModal;
