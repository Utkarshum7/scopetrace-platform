import { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { useBatchProgress } from '../hooks/useBatchProgress';

// Presentation-only lookup for job-lifecycle statuses (apps.ingestion.models
// UploadBatch.BatchStatus) — QUEUED/PROCESSING are real states a client will
// actually observe under real async dispatch (eager mode in local dev/tests
// jumps straight to a terminal status, since the task already finished by
// the time the upload response comes back).
const STATUS_PRESENTATION = {
  PENDING: { label: 'Upload Accepted — Preparing', tone: 'neutral' },
  QUEUED: { label: 'Queued for Processing', tone: 'neutral' },
  PROCESSING: { label: 'Processing…', tone: 'neutral' },
  COMPLETED: { label: 'Completed Successfully', tone: 'success' },
  PARTIALLY_COMPLETED: { label: 'Completed — Some Rows Failed', tone: 'warning' },
  FAILED: { label: 'Failed', tone: 'error' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral' },
};

const BAR_COLOR_BY_TONE = {
  neutral: 'bg-brand-500 shadow-[0_0_8px_#10b981]',
  success: 'bg-emerald-500 shadow-[0_0_8px_#10b981]',
  warning: 'bg-amber-500 shadow-[0_0_8px_#f59e0b]',
  error: 'bg-rose-500 shadow-[0_0_8px_#f43f5e]',
};

export const UploadPage = ({ setView }) => {
  const [dataSources, setDataSources] = useState([]);
  const [selectedSourceType, setSelectedSourceType] = useState('sap'); // 'sap', 'utility', 'travel'
  const [selectedDataSourceId, setSelectedDataSourceId] = useState('');
  const [file, setFile] = useState(null);

  const [isLoading, setIsLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState(null);

  // Once the 202 response comes back we have a batch_id and switch from
  // "uploading bytes" to "polling job progress" — the component only ever
  // reads { data, isTerminal } from this hook, never touches fetch/interval
  // logic directly (see hooks/useBatchProgress.js).
  const [batchId, setBatchId] = useState(null);
  const { data: progress, isTerminal } = useBatchProgress(batchId);

  // Fetch registered master data sources
  useEffect(() => {
    const fetchSources = async () => {
      try {
        const sources = await apiService.getDataSources();
        setDataSources(sources);
        
        // Pick the first default matching source
        const firstMatch = sources.find(s => s.source_type === getSourceTypeDBKey('sap'));
        if (firstMatch) setSelectedDataSourceId(firstMatch.id);
      } catch (err) {
        console.error(err);
        setErrorMsg('Failed to load registered DataSources.');
      }
    };
    fetchSources();
  }, []);

  // Utility to convert route param to Database Enum key
  const getSourceTypeDBKey = (type) => {
    switch (type) {
      case 'sap': return 'SAP_FUEL';
      case 'utility': return 'UTILITY_ELECTRICITY';
      case 'travel': return 'CORP_TRAVEL';
      default: return 'SAP_FUEL';
    }
  };

  // Adjust default selection when card is clicked
  const handleTypeSelect = (type) => {
    setSelectedSourceType(type);
    setFile(null);
    setErrorMsg(null);
    setBatchId(null);

    const dbKey = getSourceTypeDBKey(type);
    const matched = dataSources.find(s => s.source_type === dbKey);
    setSelectedDataSourceId(matched ? matched.id : '');
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setErrorMsg(null);
      setBatchId(null);
    }
  };

  const handleUploadSubmit = async (e) => {
    e.preventDefault();
    if (!file) {
      setErrorMsg('Please select a source data file to upload.');
      return;
    }
    if (!selectedDataSourceId) {
      setErrorMsg('No matching DataSource registered. Please configure one in Django Admin.');
      return;
    }

    setIsLoading(true);
    setUploadProgress(0);
    setErrorMsg(null);
    setBatchId(null);

    try {
      const result = await apiService.uploadFile(
        selectedSourceType,
        file,
        selectedDataSourceId,
        (progressEvent) => {
          const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
          setUploadProgress(percent);
        }
      );
      // Switches the component from "uploading bytes" to "polling job
      // progress" — useBatchProgress takes over from here.
      setBatchId(result.batch_id);
      setFile(null);
    } catch (err) {
      console.error(err);
      const serverErr = err.response?.data?.detail || err.response?.data?.error || 'Ingestion execution aborted.';
      setErrorMsg(serverErr);
    } finally {
      setIsLoading(false);
    }
  };

  // Filter dropdown data sources by active type card
  const activeDBKey = getSourceTypeDBKey(selectedSourceType);
  const filteredDataSources = dataSources.filter(ds => ds.source_type === activeDBKey);

  return (
    <div className="flex flex-col gap-6 animate-fadeIn">
      {/* Header */}
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-black text-white tracking-tight font-sans">
          ESG Data Ingestion Center
        </h1>
        <p className="text-xs text-slate-400">
          Upload unstructured enterprise reports. Our backend adapters will extract, validate, and secure the emission records.
        </p>
      </div>

      {/* Selector Tabs/Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* SAP Fuel */}
        <div
          onClick={() => handleTypeSelect('sap')}
          className={`cursor-pointer bg-slate-800/40 backdrop-blur-xl border rounded-xl p-5 shadow-lg flex flex-col gap-3 transition-all duration-300 ${
            selectedSourceType === 'sap'
              ? 'border-brand-500 bg-slate-800/80 shadow-brand-500/5'
              : 'border-slate-700/50 hover:border-slate-600 hover:bg-slate-800/20'
          }`}
        >
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
              Adapter Strategy #1
            </span>
            <span className={`w-2.5 h-2.5 rounded-full ${selectedSourceType === 'sap' ? 'bg-brand-500 shadow-[0_0_8px_#10b981]' : 'bg-slate-600'}`} />
          </div>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-bold text-white">SAP Fuel Ingestion</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Accepts CSV exports with German header keys, semicolons, and European comma formatting.
            </p>
          </div>
        </div>

        {/* Utility Electricity */}
        <div
          onClick={() => handleTypeSelect('utility')}
          className={`cursor-pointer bg-slate-800/40 backdrop-blur-xl border rounded-xl p-5 shadow-lg flex flex-col gap-3 transition-all duration-300 ${
            selectedSourceType === 'utility'
              ? 'border-brand-500 bg-slate-800/80 shadow-brand-500/5'
              : 'border-slate-700/50 hover:border-slate-600 hover:bg-slate-800/20'
          }`}
        >
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
              Adapter Strategy #2
            </span>
            <span className={`w-2.5 h-2.5 rounded-full ${selectedSourceType === 'utility' ? 'bg-brand-500 shadow-[0_0_8px_#10b981]' : 'bg-slate-600'}`} />
          </div>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-bold text-white">Utility Electricity Ingest</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Accepts power billing files. Normalizes kWh or MWh scales, and maps billing start/end.
            </p>
          </div>
        </div>

        {/* Corporate Travel */}
        <div
          onClick={() => handleTypeSelect('travel')}
          className={`cursor-pointer bg-slate-800/40 backdrop-blur-xl border rounded-xl p-5 shadow-lg flex flex-col gap-3 transition-all duration-300 ${
            selectedSourceType === 'travel'
              ? 'border-brand-500 bg-slate-800/80 shadow-brand-500/5'
              : 'border-slate-700/50 hover:border-slate-600 hover:bg-slate-800/20'
          }`}
        >
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
              Adapter Strategy #3
            </span>
            <span className={`w-2.5 h-2.5 rounded-full ${selectedSourceType === 'travel' ? 'bg-brand-500 shadow-[0_0_8px_#10b981]' : 'bg-slate-600'}`} />
          </div>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-bold text-white">TMC Corporate Travel</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Accepts TMC JSON exports. Calculates Haversine flight arcs and applies seating class multipliers.
            </p>
          </div>
        </div>
      </div>

      {/* Main Upload Control Panel */}
      <div className="bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl p-6 shadow-lg flex flex-col gap-5">
        <h2 className="text-base font-bold text-white font-sans tracking-tight uppercase tracking-wider text-xs text-slate-400">
          Inbound Ingestion Setup - {selectedSourceType.toUpperCase()}
        </h2>

        <form onSubmit={handleUploadSubmit} className="flex flex-col gap-5">
          {/* DataSource Dropdown */}
          <div className="flex flex-col gap-1.5 max-w-md">
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Associated Tenant DataSource
            </label>
            {filteredDataSources.length > 0 ? (
              <select
                value={selectedDataSourceId}
                onChange={(e) => setSelectedDataSourceId(e.target.value)}
                className="bg-slate-900 border border-slate-700 rounded-lg py-2.5 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 cursor-pointer"
                disabled={isLoading}
              >
                {filteredDataSources.map((ds) => (
                  <option key={ds.id} value={ds.id}>
                    {ds.name} (UUID: {ds.id.slice(0, 8)}...)
                  </option>
                ))}
              </select>
            ) : (
              <div className="p-3 bg-amber-950/20 border border-amber-500/20 text-amber-300 text-xs rounded-lg">
                No active backend DataSource registered for category <span className="font-semibold">{activeDBKey}</span>. 
                Please create one in Django Admin first.
              </div>
            )}
          </div>

          {/* Custom File Selector */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Upload Report Document
            </label>
            
            <div className="border-2 border-dashed border-slate-700 rounded-xl p-8 bg-slate-900/30 hover:bg-slate-900/50 text-center transition-all cursor-pointer relative group flex flex-col items-center justify-center gap-3">
              <input
                type="file"
                onChange={handleFileChange}
                accept={selectedSourceType === 'travel' ? '.json' : '.csv'}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                disabled={isLoading}
              />
              
              <div className="p-3.5 bg-slate-800 border border-slate-700 rounded-xl text-slate-400 group-hover:text-white transition-all">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-bold text-slate-200">
                  {file ? file.name : 'Select or drop data document'}
                </span>
                <span className="text-xs text-slate-500">
                  {file ? `${(file.size / 1024).toFixed(1)} KB` : `Supports ${selectedSourceType === 'travel' ? 'JSON' : 'CSV'} files only`}
                </span>
              </div>
            </div>
          </div>

          {/* Progress Indicator */}
          {isLoading && (
            <div className="flex flex-col gap-2">
              <div className="flex justify-between text-xs font-semibold text-slate-400">
                <span>Processing ingestion…</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="w-full bg-slate-800 rounded-full h-2">
                <div
                  className="bg-brand-500 h-2 rounded-full transition-all duration-300 shadow-[0_0_8px_#10b981]"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          {/* Feedback Blocks */}
          {errorMsg && (
            <div className="p-4 bg-rose-950/30 border border-rose-500/30 text-rose-300 text-xs rounded-xl flex items-start gap-2.5 animate-shake">
              <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <div className="flex flex-col gap-0.5">
                <span className="font-bold">Ingestion Execution Error</span>
                <span className="leading-relaxed opacity-90">{errorMsg}</span>
              </div>
            </div>
          )}

          {batchId && progress && (
            <BatchProgressCard batchId={batchId} progress={progress} isTerminal={isTerminal} setView={setView} />
          )}

          {/* Submit Trigger */}
          <div className="flex justify-end pt-2">
            <button
              type="submit"
              disabled={isLoading || (batchId && !isTerminal) || filteredDataSources.length === 0}
              className="px-6 py-2.5 bg-brand-600 hover:bg-brand-500 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-black uppercase tracking-wider rounded-lg transition-all shadow-md shadow-brand-600/10 flex items-center gap-1.5 focus:outline-none"
            >
              {isLoading && (
                <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
              Execute Ingestion Adaptor
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

// Renders whatever useBatchProgress currently has — live while polling,
// final once isTerminal flips true. Purely presentational: all of the
// "how do we know this" logic lives in the hook, not here.
const BatchProgressCard = ({ batchId, progress, isTerminal, setView }) => {
  const presentation = STATUS_PRESENTATION[progress.status] || { label: progress.status, tone: 'neutral' };
  const barColor = BAR_COLOR_BY_TONE[presentation.tone];
  const parseErrors = progress.parse_errors || [];

  return (
    <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl flex flex-col gap-3 animate-fadeIn">
      <div className="flex items-center justify-between">
        <span className="text-sm font-black text-white">{presentation.label}</span>
        <span className="font-mono text-[11px] text-slate-500">{batchId.slice(0, 8)}...</span>
      </div>

      <div className="w-full bg-slate-800 rounded-full h-2">
        <div
          className={`h-2 rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${progress.progress_percentage}%` }}
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wide">Total Rows</span>
          <span className="font-mono text-white font-bold">{progress.total_rows}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wide">Successful</span>
          <span className="font-mono text-emerald-400 font-bold">{progress.successful_records}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wide">Failed</span>
          <span className="font-mono text-rose-400 font-bold">{progress.failed_rows}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wide">
            {isTerminal ? 'Duration' : 'Elapsed'}
          </span>
          <span className="font-mono text-slate-200 font-bold">
            {progress.duration_seconds != null ? `${progress.duration_seconds.toFixed(1)}s` : '—'}
          </span>
        </div>
      </div>

      {presentation.tone === 'error' && progress.error_message && (
        <div className="text-xs text-rose-300 leading-relaxed border-t border-slate-800/60 pt-2.5">
          {progress.error_message}
        </div>
      )}

      {parseErrors.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-slate-800/60 pt-2.5">
          <span className="font-bold text-slate-300 text-xs">Parser Ingestion Errors Trace:</span>
          <ul className="list-disc list-inside space-y-1 text-rose-300 font-mono text-[11px]">
            {parseErrors.slice(0, 5).map((err, idx) => (
              <li key={idx} className="leading-relaxed">
                Row #{err.row_index}: {err.error}
              </li>
            ))}
            {parseErrors.length > 5 && (
              <li className="list-none text-slate-500 italic mt-0.5">
                ...and {parseErrors.length - 5} more validation errors.
              </li>
            )}
          </ul>
        </div>
      )}

      {isTerminal && (presentation.tone === 'success' || presentation.tone === 'warning') && (
        <div className="flex justify-end gap-3 mt-1">
          <button
            type="button"
            onClick={() => setView({ name: 'records', params: { batch: batchId } })}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all shadow-md focus:outline-none"
          >
            Review Ingested Data &rarr;
          </button>
        </div>
      )}
    </div>
  );
};

export default UploadPage;
