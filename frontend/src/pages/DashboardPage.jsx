import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';

export const DashboardPage = ({ setView }) => {
  const [batches, setBatches] = useState([]);
  const [suspiciousCount, setSuspiciousCount] = useState(0);
  const [approvedCount, setApprovedCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchDashboardData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Fetch the batch list and the suspicious/approved counts in parallel
      // (previously three sequential round-trips).
      const [fetchedBatches, suspRecords, appRecords] = await Promise.all([
        apiService.getBatches(),
        apiService.getRecords({ status: 'SUSPICIOUS' }),
        apiService.getRecords({ status: 'APPROVED' }),
      ]);
      setBatches(fetchedBatches);
      setSuspiciousCount(suspRecords.length);
      setApprovedCount(appRecords.length);
    } catch (err) {
      console.error(err);
      setError('Failed to load dashboard metrics. Please retry.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
  }, []);

  // Compute stats
  const totalBatches = batches.length;
  const completedBatchesCount = batches.filter(b => b.status === 'COMPLETED').length;
  const failedBatchesCount = batches.filter(b => b.status === 'FAILED').length;
  
  const totalProcessedRows = batches.reduce((sum, b) => sum + (b.total_rows || 0), 0);
  const totalFailedRows = batches.reduce((sum, b) => sum + (b.failed_rows || 0), 0);

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center min-h-[400px]">
        <div className="flex flex-col items-center gap-3">
          <svg className="animate-spin h-8 w-8 text-brand-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          <span className="text-sm font-medium text-slate-400">Loading emissions metrics…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 animate-fadeIn">
      {/* Page Header */}
      <div className="flex justify-between items-center">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-black text-white tracking-tight font-sans">
            ESG Command Dashboard
          </h1>
          <p className="text-xs text-slate-400">
            At-a-glance monitoring of ESG ingestion pipelines and review-ledger status
          </p>
        </div>
        <button
          onClick={fetchDashboardData}
          className="p-2 border border-slate-800 bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-white rounded-lg transition-all focus:outline-none"
          title="Refresh Data"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17" />
          </svg>
        </button>
      </div>

      {error && (
        <div className="p-4 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-sm rounded-xl">
          {error}
        </div>
      )}

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Batches Ingested */}
        <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex items-center justify-between transition-all duration-300 hover:border-brand-500/30 group">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Ingestion Batches
            </span>
            <span className="text-2xl font-black text-white font-sans tracking-tight">
              {completedBatchesCount} <span className="text-sm font-normal text-slate-500">/ {totalBatches}</span>
            </span>
            <span className="text-[10px] text-brand-400">
              Completed successfully
            </span>
          </div>
          <div className="p-3 bg-brand-500/10 rounded-xl text-brand-400 border border-brand-500/20 group-hover:bg-brand-500/20 transition-all duration-300">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
        </div>

        {/* Suspicious Anomalies */}
        <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex items-center justify-between transition-all duration-300 hover:border-amber-500/30 group">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Suspicious Anomalies
            </span>
            <span className="text-2xl font-black text-amber-400 font-sans tracking-tight">
              {suspiciousCount}
            </span>
            <span className="text-[10px] text-slate-500">
              Flagged for analyst verification
            </span>
          </div>
          <div className="p-3 bg-amber-500/10 rounded-xl text-amber-400 border border-amber-500/20 group-hover:bg-amber-500/20 transition-all duration-300">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
        </div>

        {/* Failed Rows */}
        <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex items-center justify-between transition-all duration-300 hover:border-rose-500/30 group">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Validation Failures
            </span>
            <span className="text-2xl font-black text-rose-400 font-sans tracking-tight">
              {totalFailedRows}
            </span>
            <span className="text-[10px] text-slate-500">
              Corrupt rows block audit
            </span>
          </div>
          <div className="p-3 bg-rose-500/10 rounded-xl text-rose-400 border border-rose-500/20 group-hover:bg-rose-500/20 transition-all duration-300">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
            </svg>
          </div>
        </div>

        {/* Approved Records */}
        <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex items-center justify-between transition-all duration-300 hover:border-emerald-500/30 group">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Audit Secured Logs
            </span>
            <span className="text-2xl font-black text-emerald-400 font-sans tracking-tight">
              {approvedCount}
            </span>
            <span className="text-[10px] text-emerald-400">
              Secured on immutable ledger
            </span>
          </div>
          <div className="p-3 bg-emerald-500/10 rounded-xl text-emerald-400 border border-emerald-500/20 group-hover:bg-emerald-500/20 transition-all duration-300">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </div>
        </div>
      </div>

      {/* Middle Grid: Dynamic Stats & Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Recent Batches Table (Left 2 cols) */}
        <div className="lg:col-span-2 bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4">
          <div className="flex justify-between items-center">
            <h2 className="text-base font-bold text-white font-sans tracking-tight">
              Recent Ingestion Batches
            </h2>
            <button
              onClick={() => setView({ name: 'records', params: {} })}
              className="text-xs font-semibold text-brand-400 hover:text-brand-300 transition-all focus:outline-none"
            >
              View Ingested Ledger &rarr;
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500 font-semibold uppercase tracking-wider pb-3">
                  <th className="pb-3 pr-2">File Name</th>
                  <th className="pb-3 px-2">Uploaded At</th>
                  <th className="pb-3 px-2">Source Feed</th>
                  <th className="pb-3 px-2">Status</th>
                  <th className="pb-3 pl-2 text-right">Rows</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {batches.slice(0, 5).map((b) => (
                  <tr key={b.id} className="hover:bg-slate-800/20 transition-all">
                    <td className="py-3 pr-2 font-medium text-slate-200 truncate max-w-[200px]" title={b.file_name}>
                      {b.file_name}
                    </td>
                    <td className="py-3 px-2 text-slate-400">
                      {new Date(b.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-2">
                      <span className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300 font-mono font-medium text-[10px]">
                        {b.data_source_details?.source_type || 'N/A'}
                      </span>
                    </td>
                    <td className="py-3 px-2">
                      <StatusBadge status={b.status} />
                    </td>
                    <td className="py-3 pl-2 text-right font-mono font-semibold text-slate-300">
                      {b.total_rows}
                    </td>
                  </tr>
                ))}
                {batches.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-slate-500 font-medium">
                      No files ingested yet. Go to Upload Center.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Quick Launch Cards (Right 1 col) */}
        <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4">
          <h2 className="text-base font-bold text-white font-sans tracking-tight">
            Ingestion Shortcuts
          </h2>
          <p className="text-xs text-slate-400 leading-relaxed">
            Launch a source adapter to extract, validate, and normalize raw corporate data into reviewable emission records:
          </p>

          <div className="flex flex-col gap-2.5 mt-2">
            <button
              onClick={() => setView({ name: 'upload', params: {} })}
              className="group p-3 bg-slate-900/60 border border-slate-800 hover:border-brand-500/20 hover:bg-slate-800/30 rounded-lg text-left transition-all flex items-center justify-between focus:outline-none"
            >
              <div className="flex flex-col gap-0.5">
                <span className="text-xs font-bold text-slate-200 group-hover:text-brand-400 transition-all">
                  SAP Fuel CSV Parser
                </span>
                <span className="text-[10px] text-slate-500">
                  German header & European decimal adaptors
                </span>
              </div>
              <span className="text-slate-600 group-hover:text-brand-400 group-hover:translate-x-1 transition-all">
                &rarr;
              </span>
            </button>

            <button
              onClick={() => setView({ name: 'upload', params: {} })}
              className="group p-3 bg-slate-900/60 border border-slate-800 hover:border-brand-500/20 hover:bg-slate-800/30 rounded-lg text-left transition-all flex items-center justify-between focus:outline-none"
            >
              <div className="flex flex-col gap-0.5">
                <span className="text-xs font-bold text-slate-200 group-hover:text-brand-400 transition-all">
                  Utility Portal CSV Parser
                </span>
                <span className="text-[10px] text-slate-500">
                  kWh/MWh normalization & billing periods
                </span>
              </div>
              <span className="text-slate-600 group-hover:text-brand-400 group-hover:translate-x-1 transition-all">
                &rarr;
              </span>
            </button>

            <button
              onClick={() => setView({ name: 'upload', params: {} })}
              className="group p-3 bg-slate-900/60 border border-slate-800 hover:border-brand-500/20 hover:bg-slate-800/30 rounded-lg text-left transition-all flex items-center justify-between focus:outline-none"
            >
              <div className="flex flex-col gap-0.5">
                <span className="text-xs font-bold text-slate-200 group-hover:text-brand-400 transition-all">
                  Corporate Travel JSON Parser
                </span>
                <span className="text-[10px] text-slate-500">
                  Haversine flights & DEFRA multi-seating values
                </span>
              </div>
              <span className="text-slate-600 group-hover:text-brand-400 group-hover:translate-x-1 transition-all">
                &rarr;
              </span>
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};

export default DashboardPage;
