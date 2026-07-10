/**
 * Shared page title block, previously hand-copied identically into
 * DashboardPage, RecordsPage, and UploadPage (ESGAssistantPage used a
 * smaller variant for its sidebar). `size="md"` reproduces that smaller
 * variant; `actions` (e.g. DashboardFilters) renders alongside the title
 * only when provided, matching DashboardPage's existing layout.
 */
export const PageHeader = ({ title, description, size = 'lg', actions }) => {
  const heading = (
    <div className="flex flex-col gap-1">
      <h1 className={`${size === 'lg' ? 'text-2xl' : 'text-xl'} font-black text-white tracking-tight font-sans`}>
        {title}
      </h1>
      {description && <p className="text-xs text-slate-400">{description}</p>}
    </div>
  );

  if (!actions) return heading;

  return (
    <div className="flex flex-wrap justify-between items-center gap-4">
      {heading}
      {actions}
    </div>
  );
};

export default PageHeader;
