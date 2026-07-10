import { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiService } from '../services/api';
import { useBatchProgress } from '../hooks/useBatchProgress';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { SelectableCard } from '../components/ui/SelectableCard';
import { Spinner } from '../components/ui/Spinner';

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
  success: 'bg-success-500 shadow-[0_0_8px_#10b981]',
  warning: 'bg-warning-500 shadow-[0_0_8px_#f59e0b]',
  error: 'bg-danger-500 shadow-[0_0_8px_#f43f5e]',
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
  const { data: progress, isTerminal, error: progressError } = useBatchProgress(batchId);
  const queryClient = useQueryClient();

  // Phase 7.5 (H4-6): invalidate dashboard queries (TanStack Query, 60s
  // staleTime, see useWidgetData) exactly when the batch reaches a terminal
  // state -- NOT at upload-submit time, when the ingest/calculate chain
  // hasn't run yet and nothing has actually changed server-side. Runs once
  // per terminal transition (isTerminal flips false->true per batchId).
  useEffect(() => {
    if (isTerminal) {
      queryClient.invalidateQueries();
    }
  }, [isTerminal, batchId, queryClient]);

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

  // Adjust default selection when a SelectableCard is activated (click or
  // Enter/Space -- SelectableCard normalizes both to one onSelect call) --
  // the 3 adapter cards behave exactly like a radio group (single selection
  // among mutually exclusive options).
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
      <PageHeader
        title="ESG Data Ingestion Center"
        description="Upload unstructured enterprise reports. Our backend adapters will extract, validate, and secure the emission records."
      />

      {/* Selector Tabs/Cards -- a radio group: exactly one adapter strategy
          is selected at a time. Each card is individually Tab-reachable
          (not roving tabindex) with Enter/Space to activate; simpler than
          the full WAI-ARIA radiogroup arrow-key pattern while still
          announcing correctly ("radio button, N of 3, selected/not
          selected") to screen readers -- a deliberate scope trade-off. */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4" role="radiogroup" aria-label="Ingestion adapter strategy">
        <SelectableCard
          selected={selectedSourceType === 'sap'}
          onSelect={() => handleTypeSelect('sap')}
          eyebrow="Adapter Strategy #1"
          title="SAP Fuel Ingestion"
          description="Accepts CSV exports with German header keys, semicolons, and European comma formatting."
        />
        <SelectableCard
          selected={selectedSourceType === 'utility'}
          onSelect={() => handleTypeSelect('utility')}
          eyebrow="Adapter Strategy #2"
          title="Utility Electricity Ingest"
          description="Accepts power billing files. Normalizes kWh or MWh scales, and maps billing start/end."
        />
        <SelectableCard
          selected={selectedSourceType === 'travel'}
          onSelect={() => handleTypeSelect('travel')}
          eyebrow="Adapter Strategy #3"
          title="TMC Corporate Travel"
          description="Accepts TMC JSON exports. Calculates Haversine flight arcs and applies seating class multipliers."
        />
      </div>

      {/* Main Upload Control Panel */}
      <Card className="p-6 flex flex-col gap-5">
        <h2 className="text-base font-bold text-white font-sans tracking-tight uppercase tracking-wider text-xs text-slate-400">
          Inbound Ingestion Setup - {selectedSourceType.toUpperCase()}
        </h2>

        <form onSubmit={handleUploadSubmit} className="flex flex-col gap-5">
          {/* DataSource Dropdown */}
          <div className="flex flex-col gap-1.5 max-w-md">
            <label htmlFor="upload-data-source" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Associated Tenant DataSource
            </label>
            {filteredDataSources.length > 0 ? (
              <select
                id="upload-data-source"
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
              <div className="p-3 bg-warning-950/20 border border-warning-500/20 text-warning-300 text-xs rounded-lg">
                No active backend DataSource registered for category <span className="font-semibold">{activeDBKey}</span>.
                Please create one in Django Admin first.
              </div>
            )}
          </div>

          {/* Custom File Selector */}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="upload-file-input" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Upload Report Document
            </label>

            <div className="border-2 border-dashed border-slate-700 rounded-xl p-8 bg-slate-900/30 hover:bg-slate-900/50 text-center transition-all cursor-pointer relative group flex flex-col items-center justify-center gap-3 focus-within:ring-2 focus-within:ring-brand-500">
              <input
                id="upload-file-input"
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
            <div className="flex flex-col gap-2" role="status" aria-live="polite">
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
            <div role="alert" className="p-4 bg-danger-950/30 border border-danger-500/30 text-danger-300 text-xs rounded-xl flex items-start gap-2.5 animate-shake">
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

          {/* Phase 8 (8d): the gap between "upload accepted" and the first
              batch-progress poll resolving (up to ~1.5s -- see
              hooks/useBatchProgress's POLL_INTERVAL_MS) previously showed
              nothing at all. Also surfaces a genuine progress-poll failure,
              which was silently swallowed before (the hook's `error` was
              never read) -- the ingestion job itself may well still be
              running server-side even if we can't currently check on it. */}
          {batchId && !progress && !progressError && (
            <div className="flex items-center gap-2 text-xs text-slate-400" role="status" aria-live="polite">
              <Spinner className="h-3.5 w-3.5 text-brand-500" />
              Waiting for job status…
            </div>
          )}
          {batchId && !progress && progressError && (
            <div role="alert" className="p-3 bg-warning-950/20 border border-warning-500/20 text-warning-300 text-xs rounded-lg">
              Couldn&apos;t check the ingestion job&apos;s status. The upload was accepted and may still be
              processing — check the Review Ledger shortly, or refresh this page to try checking again.
            </div>
          )}

          {/* Submit Trigger */}
          <div className="flex justify-end pt-2">
            <button
              type="submit"
              disabled={isLoading || (batchId && !isTerminal) || filteredDataSources.length === 0}
              className="px-6 py-2.5 bg-brand-600 hover:bg-brand-500 disabled:bg-slate-800 disabled:text-slate-600 text-white text-xs font-black uppercase tracking-wider rounded-lg transition-all shadow-md shadow-brand-600/10 flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              {isLoading && <Spinner className="h-4 w-4 text-white" />}
              Execute Ingestion Adaptor
            </button>
          </div>
        </form>
      </Card>
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
      <div className="flex items-center justify-between" role="status" aria-live="polite">
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
          <span className="font-mono text-success-400 font-bold">{progress.successful_records}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wide">Failed</span>
          <span className="font-mono text-danger-400 font-bold">{progress.failed_rows}</span>
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

      {presentation.tone === 'error' && (
        <div className="text-xs text-danger-300 leading-relaxed border-t border-slate-800/60 pt-2.5 flex flex-col gap-1">
          {progress.error_message && <span>{progress.error_message}</span>}
          <span className="text-slate-500">Select a new file above and try again.</span>
        </div>
      )}

      {parseErrors.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-slate-800/60 pt-2.5">
          <span className="font-bold text-slate-300 text-xs">Parser Ingestion Errors Trace:</span>
          <ul className="list-disc list-inside space-y-1 text-danger-300 font-mono text-[11px]">
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
            className="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white text-xs font-bold uppercase tracking-wider rounded-lg transition-all shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Review Ingested Data &rarr;
          </button>
        </div>
      )}
    </div>
  );
};

export default UploadPage;
