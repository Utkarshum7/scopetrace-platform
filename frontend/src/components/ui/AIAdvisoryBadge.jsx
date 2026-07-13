/**
 * Shared "AI Advisory" pill -- marks AI-generated content as advisory-only,
 * never a governance decision. Previously hand-duplicated with drifting
 * modifier classes in AIInsightsPanel, CommonWidgets' ReportsWidget, and
 * ESGAssistantPage; consolidated here (Phase 8, 8d).
 */
export const AIAdvisoryBadge = ({ className = '' }) => (
  <span
    className={`px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-400/30 text-indigo-200 text-[9px] tracking-wide ${className}`}
  >
    AI Advisory
  </span>
);

export default AIAdvisoryBadge;
