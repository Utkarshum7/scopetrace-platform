import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';
import { FilterBar } from '../components/FilterBar';
import { ApprovalModal } from '../components/ApprovalModal';

export const RecordsPage = ({ initialFilters = {} }) => {
  const [records, setRecords] = useState([]);
  const [dataSources, setDataSources] = useState([]);
  const [batches, setBatches] = useState([]);
  
  const [filters, setFilters] = useState({
    data_source: initialFilters.data_source || '',
    batch: initialFilters.batch || '',
    status: initialFilters.status || '',
    suspicious: initialFilters.suspicious || '',
  });

  const [isLoading, setIsLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState(null);

  // Selected Record for Details Drawer & Approval Modal
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [recordToApprove, setRecordToApprove] = useState(null);
  const [isApprovalOpen, setIsApprovalOpen] = useState(false);

  // Fetch static dropdowns
  useEffect(() => {
    const fetchMasterData = async () => {
      try {
        const [sources, fetchedBatches] = await Promise.all([
          apiService.getDataSources(),
          apiService.getBatches(),
        ]);
        setDataSources(sources);
        setBatches(fetchedBatches);
      } catch (err) {
        console.error(err);
      }
    };
    fetchMasterData();
  }, []);

  // Fetch records dynamically based on active filters
  const fetchRecords = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      // Map empty strings to undefined to clean parameters
      const cleanParams = {};
      Object.keys(filters).forEach((key) => {
        if (filters[key] !== '') cleanParams[key] = filters[key];
      });

      const data = await apiService.getRecords(cleanParams);
      setRecords(data);
    } catch (err) {
      console.error(err);
      setErrorMsg('Failed to query emission records database.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchRecords();
  }, [filters]);

  const handleRecordApproveSuccess = () => {
    // Refresh records list to reflect approval status change
    fetchRecords();
    setSelectedRecord(null); // Clear selected drawer details
  };

  return (
    <div className="flex flex-col gap-6 animate-fadeIn">
      {/* Header */}
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-black text-white tracking-tight font-sans">
          Analyst Ingestion Ledger
        </h1>
        <p className="text-xs text-slate-400">
          Review, filter, inspect raw source payloads, and approve emission records. Approved records are locked for audit trail compliance.
        </p>
      </div>

      {/* Reusable FilterBar */}
      <FilterBar
        dataSources={dataSources}
        batches={batches}
        filters={filters}
        onFilterChange={setFilters}
      />

      {errorMsg && (
        <div className="p-4 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-sm rounded-xl">
          {errorMsg}
        </div>
      )}

      {/* Main Ledger Grid & Details Panel */}
      <div className="flex flex-col lg:flex-row gap-6 items-start">
        
        {/* Records Table (Left Column) */}
        <div className="flex-1 w-full bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4">
          
          <div className="flex justify-between items-center pb-2 border-b border-slate-800/60">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Record Audit Stream ({records.length} items found)
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500 font-semibold uppercase tracking-wider pb-3">
                  <th className="pb-3 pr-2">Row</th>
                  <th className="pb-3 px-2">Scope</th>
                  <th className="pb-3 px-2">Source Unit</th>
                  <th className="pb-3 px-2 text-right">Calculated CO₂e</th>
                  <th className="pb-3 px-2">Status</th>
                  <th className="pb-3 pl-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {records.map((r) => {
                  const isSuspicious = r.is_suspicious;
                  const isFailed = r.status === 'FAILED';
                  const isApproved = r.status === 'APPROVED';

                  let rowBg = 'hover:bg-slate-800/20';
                  if (isSuspicious) rowBg = 'bg-amber-950/10 hover:bg-amber-950/20';
                  if (isFailed) rowBg = 'bg-rose-950/10 hover:bg-rose-950/20';

                  return (
                    <tr
                      key={r.id}
                      className={`cursor-pointer transition-all ${rowBg} ${
                        selectedRecord?.id === r.id ? 'bg-slate-700/20 border-l-2 border-brand-500' : ''
                      }`}
                      onClick={() => setSelectedRecord(r)}
                    >
                      <td className="py-3.5 pr-2 font-mono text-slate-400">
                        #{r.row_index}
                      </td>
                      <td className="py-3.5 px-2">
                        <span className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-brand-400 font-medium tracking-wide">
                          {r.scope_category}
                        </span>
                      </td>
                      <td className="py-3.5 px-2 text-slate-300 font-mono">
                        {r.raw_data_payload?.unit || r.normalized_unit || 'N/A'}
                      </td>
                      <td className="py-3.5 px-2 text-right font-mono font-bold text-slate-200">
                        {r.normalized_value ? parseFloat(r.normalized_value).toLocaleString(undefined, {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        }) : '-'}
                      </td>
                      <td className="py-3.5 px-2">
                        <StatusBadge status={r.status} />
                      </td>
                      <td className="py-3.5 pl-2 text-right" onClick={(e) => e.stopPropagation()}>
                        <button
                          disabled={isApproved || isFailed}
                          onClick={() => {
                            setRecordToApprove(r);
                            setIsApprovalOpen(true);
                          }}
                          className={`px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all focus:outline-none ${
                            isApproved
                              ? 'bg-emerald-950/20 border border-emerald-500/20 text-emerald-500/50 cursor-not-allowed'
                              : isFailed
                              ? 'bg-rose-950/20 border border-rose-500/20 text-rose-500/50 cursor-not-allowed'
                              : 'bg-brand-600 hover:bg-brand-500 text-white shadow-md shadow-brand-600/10'
                          }`}
                        >
                          {isApproved ? 'Secured' : isFailed ? 'Blocked' : 'Approve'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {records.length === 0 && !isLoading && (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-slate-500 font-medium">
                      No records match the active filter criteria.
                    </td>
                  </tr>
                )}
                {isLoading && (
                  <tr>
                    <td colSpan={6} className="py-12 text-center text-slate-500 font-medium">
                      Querying emissions ledger…
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Dynamic Detail Drawer (Right Column) */}
        {selectedRecord && (
          <div className="w-full lg:w-[380px] bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4 animate-slideIn">
            
            {/* Drawer Header */}
            <div className="flex justify-between items-start pb-2 border-b border-slate-800/60">
              <div className="flex flex-col gap-0.5">
                <h3 className="text-sm font-bold text-white font-sans">
                  Record Audit Metadata
                </h3>
                <span className="text-[10px] text-slate-500 font-mono">
                  UUID: {selectedRecord.id.slice(0, 18)}...
                </span>
              </div>
              <button
                onClick={() => setSelectedRecord(null)}
                className="text-slate-500 hover:text-slate-300 transition-all p-0.5"
              >
                ✕
              </button>
            </div>

            {/* Validation & Error blocks */}
            {selectedRecord.is_suspicious && (
              <div className="p-3 bg-amber-950/30 border border-amber-500/30 text-amber-300 text-xs rounded-lg flex flex-col gap-1 animate-pulse">
                <span className="font-bold flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 bg-amber-400 rounded-full" />
                  Validation Warning Flags:
                </span>
                <ul className="list-disc list-inside space-y-0.5 opacity-90 pl-1 font-mono text-[10px]">
                  {Object.entries(selectedRecord.validation_errors || {}).map(([key, val]) => (
                    <li key={key}>{val}</li>
                  ))}
                </ul>
              </div>
            )}

            {selectedRecord.status === 'FAILED' && (
              <div className="p-3 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-xs rounded-lg flex flex-col gap-1">
                <span className="font-bold flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 bg-rose-500 rounded-full" />
                  Ingestion Validation Failures:
                </span>
                <ul className="list-disc list-inside space-y-0.5 opacity-90 pl-1 font-mono text-[10px]">
                  {Object.entries(selectedRecord.validation_errors || {}).map(([key, val]) => (
                    <li key={key}>{val}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Approval Metadata */}
            {selectedRecord.status === 'APPROVED' && (
              <div className="p-3 bg-emerald-950/30 border border-emerald-500/30 text-emerald-300 text-xs rounded-lg flex flex-col gap-1.5">
                <span className="font-bold flex items-center gap-1.5 text-white">
                  <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  Secured Audit Lock Trail:
                </span>
                <div className="flex flex-col gap-1 text-[11px] font-mono leading-relaxed pl-1 text-emerald-200">
                  <div>Approved By ID: {selectedRecord.approved_by || 'Anonymous'}</div>
                  <div>Timestamp: {new Date(selectedRecord.approved_at).toLocaleString()}</div>
                </div>
              </div>
            )}

            {/* Raw JSON Payload Viewer */}
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Raw Source File Payload
              </span>
              <div className="bg-slate-950/70 border border-slate-800 rounded-lg p-3 overflow-auto max-h-[250px] font-mono text-[10px] text-slate-400 leading-normal">
                <pre>{JSON.stringify(selectedRecord.raw_data_payload, null, 2)}</pre>
              </div>
            </div>

            {/* Extra Calculations Summary */}
            <div className="bg-slate-900/60 rounded-lg p-3.5 border border-slate-800/80 flex flex-col gap-2 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Normalizing Scale:</span>
                <span className="font-semibold text-slate-300">
                  {selectedRecord.raw_data_payload?.quantity || 'N/A'} &rarr; {selectedRecord.normalized_value ? parseFloat(selectedRecord.normalized_value).toLocaleString() : '0'} {selectedRecord.normalized_unit}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Creation Date:</span>
                <span className="font-semibold text-slate-300">
                  {new Date(selectedRecord.created_at).toLocaleDateString()}
                </span>
              </div>
            </div>

          </div>
        )}

      </div>

      {/* Approval Reasoning Dialog Modal */}
      <ApprovalModal
        isOpen={isApprovalOpen}
        record={recordToApprove}
        onClose={() => {
          setIsApprovalOpen(false);
          setRecordToApprove(null);
        }}
        onApproved={handleRecordApproveSuccess}
      />
    </div>
  );
};

export default RecordsPage;
