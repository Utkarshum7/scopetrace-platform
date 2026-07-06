import { useState } from 'react';
import { apiService } from '../services/api';

/**
 * ApprovalModal Component
 * Prompts the analyst for an optional justification/reason, executes the approve API,
 * and handles transitional and state errors cleanly with premium micro-interactions.
 */
export const ApprovalModal = ({ isOpen, record, onClose, onApproved }) => {
  const [reason, setReason] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);

  if (!isOpen || !record) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMsg(null);

    try {
      await apiService.approveRecord(record.id, reason.trim());
      onApproved();
      setReason('');
      onClose();
    } catch (error) {
      const apiErr = error.response?.data?.detail || 'Approval submission failed.';
      setErrorMsg(apiErr);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Overlay Backdrop */}
      <div
        className="absolute inset-0 bg-slate-950/85 backdrop-blur-sm transition-opacity duration-300"
        onClick={onClose}
      />

      {/* Modal Dialog Body */}
      <div className="relative w-full max-w-md bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl z-10 transition-all duration-300 transform scale-100 flex flex-col gap-4">
        
        {/* Header */}
        <div className="flex justify-between items-start">
          <div className="flex flex-col gap-1">
            <h3 className="text-lg font-bold text-white font-sans tracking-tight">
              Approve Emission Record
            </h3>
            <p className="text-xs text-slate-400">
              Validating Row Index #{record.row_index} in batch
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 hover:bg-slate-800/80 rounded transition-all focus:outline-none"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
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
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Approval Rationale / Reason
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Verified with supplier invoices; outlier due to seasonal Q1 production surge."
              rows={3}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all resize-none"
              disabled={isLoading}
            />
          </div>

          {errorMsg && (
            <div className="p-3 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-xs rounded-lg flex items-center gap-2 animate-shake">
              <svg
                className="w-4 h-4 flex-shrink-0"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                xmlns="http://www.w3.org/2000/svg"
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

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-slate-800 bg-slate-900 hover:bg-slate-800/80 text-slate-300 text-xs font-bold uppercase tracking-wider rounded-lg transition-all focus:outline-none"
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-5 py-2 bg-brand-600 hover:bg-brand-500 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all shadow-md shadow-brand-600/10 flex items-center gap-1.5 focus:outline-none"
              disabled={isLoading}
            >
              {isLoading && (
                <svg
                  className="animate-spin h-3.5 h-3.5 text-white"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
              )}
              Confirm & Lock
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default ApprovalModal;
