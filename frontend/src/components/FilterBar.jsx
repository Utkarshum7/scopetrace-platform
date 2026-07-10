/**
 * FilterBar Component
 * Renders an inline, dynamic filter control bar styled with premium dark-glassmorphic panels.
 */
export const FilterBar = ({
  dataSources = [],
  batches = [],
  filters = {},
  onFilterChange,
}) => {
  const handleSelectChange = (e) => {
    const { name, value } = e.target;
    onFilterChange({
      ...filters,
      [name]: value === 'ALL' ? '' : value,
    });
  };

  const handleCheckboxChange = (e) => {
    const { name, checked } = e.target;
    onFilterChange({
      ...filters,
      [name]: checked ? 'true' : '',
    });
  };

  const handleReset = () => {
    onFilterChange({
      data_source: '',
      batch: '',
      status: '',
      suspicious: '',
    });
  };

  return (
    <div className="bg-slate-800/60 backdrop-blur-xl border border-slate-700/60 rounded-xl p-5 shadow-lg flex flex-wrap gap-4 items-end transition-all duration-300">
      
      {/* DataSource Selector */}
      <div className="flex-1 min-w-[200px] flex flex-col gap-1.5">
        <label htmlFor="filter-data-source" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Data Feed Source
        </label>
        <select
          id="filter-data-source"
          name="data_source"
          value={filters.data_source || 'ALL'}
          onChange={handleSelectChange}
          className="w-full bg-slate-900 border border-slate-700 rounded-lg py-2 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all cursor-pointer"
        >
          <option value="ALL">All Sources</option>
          {dataSources.map((ds) => (
            <option key={ds.id} value={ds.id}>
              {ds.name} ({ds.source_type})
            </option>
          ))}
        </select>
      </div>

      {/* Batch Selector */}
      <div className="flex-1 min-w-[200px] flex flex-col gap-1.5">
        <label htmlFor="filter-batch" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Ingestion Batch
        </label>
        <select
          id="filter-batch"
          name="batch"
          value={filters.batch || 'ALL'}
          onChange={handleSelectChange}
          className="w-full bg-slate-900 border border-slate-700 rounded-lg py-2 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all cursor-pointer"
        >
          <option value="ALL">All Batches</option>
          {batches.map((batch) => (
            <option key={batch.id} value={batch.id}>
              {batch.file_name} ({new Date(batch.created_at).toLocaleDateString()})
            </option>
          ))}
        </select>
      </div>

      {/* Status Selector */}
      <div className="w-[180px] flex flex-col gap-1.5">
        <label htmlFor="filter-status" className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Record Status
        </label>
        <select
          id="filter-status"
          name="status"
          value={filters.status || 'ALL'}
          onChange={handleSelectChange}
          className="w-full bg-slate-900 border border-slate-700 rounded-lg py-2 px-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition-all cursor-pointer"
        >
          <option value="ALL">All Statuses</option>
          <option value="DRAFT">Draft / Unverified</option>
          <option value="SUSPICIOUS">Suspicious Only</option>
          <option value="APPROVED">Approved & Locked</option>
          <option value="FAILED">Failed Ingest</option>
        </select>
      </div>

      {/* Suspicious Filter Toggle */}
      <div className="h-[38px] flex items-center gap-2.5 px-3 border border-slate-700/80 bg-slate-900/40 rounded-lg select-none cursor-pointer hover:bg-slate-900/60 transition-all">
        <input
          type="checkbox"
          id="suspicious-toggle"
          name="suspicious"
          checked={filters.suspicious === 'true'}
          onChange={handleCheckboxChange}
          className="w-4 h-4 rounded text-brand-600 bg-slate-800 border-slate-700 focus:ring-brand-500 accent-brand-500 cursor-pointer"
        />
        <label
          htmlFor="suspicious-toggle"
          className="text-xs font-medium text-amber-300 cursor-pointer"
        >
          Show Anomalies Only
        </label>
      </div>

      {/* Reset Controls Button */}
      <button
        onClick={handleReset}
        className="h-[38px] px-4 py-2 bg-slate-700/60 border border-slate-600/50 hover:bg-slate-700 hover:text-white text-slate-300 text-xs font-semibold uppercase tracking-wider rounded-lg transition-all shadow-md focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2 focus:ring-offset-slate-900"
      >
        Clear Filters
      </button>
    </div>
  );
};

export default FilterBar;
