import { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';
import { FilterBar } from '../components/FilterBar';
import { ApprovalModal } from '../components/ApprovalModal';
import { AIInsightsPanel } from '../components/AIInsightsPanel';

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

  // Pagination
  const [page, setPage] = useState(1);
  const [pageInfo, setPageInfo] = useState({ count: 0, next: null, previous: null });

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

      cleanParams.page = page;
      const data = await apiService.getRecords(cleanParams);
      setRecords(data.items);
      setPageInfo({ count: data.count, next: data.next, previous: data.previous });
    } catch (err) {
      console.error(err);
      setErrorMsg('Failed to query emission records database.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchRecords();
  }, [filters, page]);

  const handleWorkflowActionComplete = () => {
    // Refresh records list to reflect the workflow status change
    fetchRecords();
    setSelectedRecord(null); // Clear selected drawer details
  };

  const handleExport = () => {
    const cleanParams = {};
    Object.keys(filters).forEach((key) => {
      if (filters[key] !== '') cleanParams[key] = filters[key];
    });
    apiService.exportRecords(cleanParams);
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
        onFilterChange={(f) => { setPage(1); setFilters(f); }}
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
              Record Audit Stream ({pageInfo.count} total)
            </span>
            <button
              onClick={handleExport}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800 hover:text-white text-[11px] font-semibold uppercase tracking-wider transition-all focus:outline-none"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Export CSV
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500 font-semibold uppercase tracking-wider pb-3">
                  <th className="pb-3 pr-2">Row</th>
                  <th className="pb-3 px-2">Scope</th>
                  <th className="pb-3 px-2">Source Unit</th>
                  <th className="pb-3 px-2 text-right">Normalized Value</th>
                  <th className="pb-3 px-2 text-right">CO₂e (t)</th>
                  <th className="pb-3 px-2">Status</th>
                  <th className="pb-3 pl-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {records.map((r) => {
                  const isSuspicious = r.is_suspicious;
                  const isFailed = r.status === 'FAILED';
                  const isApproved = r.status === 'APPROVED';
                  const isSubmitted = r.status === 'SUBMITTED';
                  const isRejected = r.status === 'REJECTED';
                  const isActionable = !isApproved && !isFailed;

                  let actionLabel = 'Submit';
                  if (isApproved) actionLabel = 'Secured';
                  else if (isFailed) actionLabel = 'Blocked';
                  else if (isSubmitted) actionLabel = 'Review';
                  else if (isRejected) actionLabel = 'Resubmit';

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
                        {r.normalized_value && r.normalized_unit ? (
                          <span className="ml-1 font-normal text-slate-500">{r.normalized_unit}</span>
                        ) : null}
                      </td>
                      <td className="py-3.5 px-2 text-right font-mono font-bold">
                        {r.co2e_tonnes != null ? (
                          <span className="text-emerald-300">
                            {parseFloat(r.co2e_tonnes).toLocaleString(undefined, {
                              minimumFractionDigits: 3,
                              maximumFractionDigits: 3,
                            })}
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.5 rounded bg-amber-950/30 border border-amber-500/20 text-amber-400 text-[9px] uppercase tracking-wide">
                            Unresolved
                          </span>
                        )}
                      </td>
                      <td className="py-3.5 px-2">
                        <StatusBadge status={r.status} />
                      </td>
                      <td className="py-3.5 pl-2 text-right" onClick={(e) => e.stopPropagation()}>
                        <button
                          disabled={!isActionable}
                          onClick={() => {
                            setRecordToApprove(r);
                            setIsApprovalOpen(true);
                          }}
                          className={`px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all focus:outline-none ${
                            isApproved
                              ? 'bg-emerald-950/20 border border-emerald-500/20 text-emerald-500/50 cursor-not-allowed'
                              : isFailed
                              ? 'bg-rose-950/20 border border-rose-500/20 text-rose-500/50 cursor-not-allowed'
                              : isSubmitted
                              ? 'bg-violet-600 hover:bg-violet-500 text-white shadow-md shadow-violet-600/10'
                              : 'bg-brand-600 hover:bg-brand-500 text-white shadow-md shadow-brand-600/10'
                          }`}
                        >
                          {actionLabel}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {records.length === 0 && !isLoading && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-slate-500 font-medium">
                      No records match the active filter criteria.
                    </td>
                  </tr>
                )}
                {isLoading && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-slate-500 font-medium">
                      Querying emissions ledger…
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination controls */}
          {(pageInfo.next || pageInfo.previous) && (
            <div className="flex items-center justify-between pt-2 border-t border-slate-800/60 text-xs">
              <span className="text-slate-500">Page {page}</span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={!pageInfo.previous || isLoading}
                  className="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-800 transition-all focus:outline-none"
                >
                  ← Prev
                </button>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={!pageInfo.next || isLoading}
                  className="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-slate-800 transition-all focus:outline-none"
                >
                  Next →
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Dynamic Detail Drawer (Right Column) */}
        {selectedRecord && (
          <div className="w-full lg:w-drawer bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-5 shadow-lg flex flex-col gap-4 animate-slideIn">
            
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
                    <li key={key}>
                      <span className="text-slate-400">{key}:</span>{' '}
                      {Array.isArray(val) ? val.join('; ') : String(val)}
                    </li>
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
                    <li key={key}>
                      <span className="text-slate-400">{key}:</span>{' '}
                      {Array.isArray(val) ? val.join('; ') : String(val)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* AI Insights — Phase 7b, advisory only, renders nothing if empty */}
            <AIInsightsPanel recordId={selectedRecord.id} />

            {selectedRecord.status === 'SUBMITTED' && (
              <div className="p-3 bg-violet-950/30 border border-violet-500/30 text-violet-300 text-xs rounded-lg flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-violet-400 rounded-full" />
                <span className="font-medium">Awaiting approval or rejection.</span>
              </div>
            )}

            {selectedRecord.status === 'REJECTED' && (
              <div className="p-3 bg-orange-950/30 border border-orange-500/30 text-orange-300 text-xs rounded-lg flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-orange-400 rounded-full" />
                <span className="font-medium">Rejected — needs correction, then resubmission.</span>
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

            {/* Carbon Calculation Breakdown (explainability) */}
            {selectedRecord.calculation_status === 'CALCULATED' && selectedRecord.calculation_trace?.steps ? (
              <div className="p-3 bg-emerald-950/20 border border-emerald-500/20 rounded-lg flex flex-col gap-2">
                <span className="text-xs font-bold text-emerald-300 uppercase tracking-wider">
                  Carbon Calculation
                </span>
                <div className="flex flex-col gap-1.5">
                  {selectedRecord.calculation_trace.steps.map((step, i) => (
                    <div key={i} className="flex justify-between items-baseline gap-3 text-[11px]">
                      <span className="text-slate-500 shrink-0">{step.label}</span>
                      <span className="font-mono text-slate-200 text-right">
                        {step.value}
                        {step.source ? <span className="ml-1 text-slate-500">({step.source})</span> : null}
                      </span>
                    </div>
                  ))}
                </div>
                {selectedRecord.factor_provenance && (
                  <div className="mt-1 pt-2 border-t border-emerald-500/10 text-[10px] text-slate-500 font-mono leading-relaxed">
                    Factor: {selectedRecord.factor_provenance.publisher} {selectedRecord.factor_provenance.version} · {selectedRecord.factor_provenance.factor_value} kgCO₂e/{selectedRecord.factor_provenance.factor_unit}
                  </div>
                )}
              </div>
            ) : (
              <div className="p-3 bg-amber-950/20 border border-amber-500/20 rounded-lg text-amber-300 text-xs leading-relaxed">
                <span className="font-bold">CO₂e not computed.</span> No emission factor matched this record
                {selectedRecord.calculation_status ? ` (${selectedRecord.calculation_status})` : ''}. An Org Admin can add
                an activity mapping / factor and recalculate.
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
        onActionComplete={handleWorkflowActionComplete}
      />
    </div>
  );
};

export default RecordsPage;
