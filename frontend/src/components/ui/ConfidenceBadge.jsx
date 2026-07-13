/**
 * Shared AI-confidence pill. The color map and badge markup were
 * hand-duplicated verbatim in three places (AIInsightsPanel,
 * ESGAssistantPage, CommonWidgets' NARRATION_CONFIDENCE_STYLES) --
 * consolidated here as the single source of truth.
 */
const CONFIDENCE_STYLES = {
  LOW: 'bg-slate-800/60 border-slate-700 text-slate-400',
  MEDIUM: 'bg-amber-950/30 border-amber-500/30 text-amber-300',
  HIGH: 'bg-rose-950/30 border-rose-500/30 text-rose-300',
};

export const ConfidenceBadge = ({ confidence, className = '' }) => (
  <span
    className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wide ${
      CONFIDENCE_STYLES[confidence] || CONFIDENCE_STYLES.LOW
    } ${className}`}
  >
    {confidence} confidence
  </span>
);

export default ConfidenceBadge;
