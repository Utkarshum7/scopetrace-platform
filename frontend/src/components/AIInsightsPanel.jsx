import { useEffect, useState } from 'react';
import { apiService } from '../services/api';

/**
 * AIInsightsPanel — Phase 7b (anomaly explanations), extended in Phase 7c
 * with factor recommendations and Phase 7d with validation assistance.
 * Read-only, advisory-only: renders whatever apps.ai already generated
 * (GET /api/records/{id}/ai-annotations/ and
 * GET /api/records/{id}/factor-recommendations/), never triggers
 * generation itself and never offers any action that could be mistaken
 * for a governance decision. Renders nothing while loading or if NONE of
 * anomaly annotations, factor recommendations, or validation assistance
 * exist yet — this is deliberately a passive, expandable addition to the
 * existing detail drawer, not a new page or a required step in the review
 * flow.
 *
 * Anomaly explanations and validation assistance both come back from the
 * SAME /ai-annotations/ endpoint (apps.ai.models.AIAnnotation is shared
 * across both capabilities -- see ADR 0011), split here client-side by
 * `capability` into two visually distinct sections.
 */
const CONFIDENCE_STYLES = {
  LOW: 'bg-slate-800/60 border-slate-700 text-slate-400',
  MEDIUM: 'bg-amber-950/30 border-amber-500/30 text-amber-300',
  HIGH: 'bg-rose-950/30 border-rose-500/30 text-rose-300',
};

export const AIInsightsPanel = ({ recordId }) => {
  const [annotations, setAnnotations] = useState([]);
  const [factorRecommendations, setFactorRecommendations] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isExpanded, setIsExpanded] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    Promise.all([
      apiService.getRecordAIAnnotations(recordId).catch(() => []),
      apiService.getRecordFactorRecommendations(recordId).catch(() => []),
    ])
      .then(([annotationsData, factorRecommendationsData]) => {
        if (cancelled) return;
        setAnnotations(annotationsData);
        setFactorRecommendations(factorRecommendationsData);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [recordId]);

  const anomalyAnnotations = annotations.filter((a) => a.capability === 'ANOMALY_DETECTION');
  const validationAnnotations = annotations.filter((a) => a.capability === 'VALIDATION_ASSISTANCE');

  if (
    isLoading ||
    (anomalyAnnotations.length === 0 && factorRecommendations.length === 0 && validationAnnotations.length === 0)
  ) {
    return null;
  }

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-950/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded((v) => !v)}
        aria-expanded={isExpanded}
        aria-controls="ai-insights-panel-content"
        className="w-full flex items-center justify-between p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-lg"
      >
        <span className="flex items-center gap-1.5 text-xs font-bold text-indigo-300 uppercase tracking-wider">
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
          AI Insights
          <span className="ml-1 px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-400/30 text-indigo-200 text-[9px] tracking-wide">
            AI Advisory
          </span>
        </span>
        <span className="text-indigo-400 text-xs">{isExpanded ? '▲' : '▼'}</span>
      </button>

      {isExpanded && (
        <div id="ai-insights-panel-content" className="px-3 pb-3 flex flex-col gap-3">
          {anomalyAnnotations.map((a, idx) => (
            <div
              key={a.id}
              className={`flex flex-col gap-2 pt-2 ${idx > 0 ? 'border-t border-indigo-500/10' : ''}`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-indigo-300 uppercase tracking-wider">
                  {a.capability.replace(/_/g, ' ').toLowerCase()}
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wide ${
                    CONFIDENCE_STYLES[a.confidence] || CONFIDENCE_STYLES.LOW
                  }`}
                >
                  {a.confidence} confidence
                </span>
              </div>

              <p className="text-[11px] text-slate-300 leading-relaxed">{a.explanation}</p>

              {a.contributing_factors?.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Evidence
                  </span>
                  <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                    {a.contributing_factors.map((factor, i) => (
                      <li key={i}>{factor}</li>
                    ))}
                  </ul>
                </div>
              )}

              {a.suggested_investigation && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Recommendation
                  </span>
                  <p className="text-[11px] text-indigo-200/90 leading-relaxed">
                    {a.suggested_investigation}
                  </p>
                </div>
              )}

              <span className="text-[9px] text-slate-600 font-mono">
                {new Date(a.created_at).toLocaleString()}
              </span>
            </div>
          ))}

          {factorRecommendations.map((r, idx) => (
            <div
              key={r.id}
              className={`flex flex-col gap-2 pt-2 ${
                idx > 0 || anomalyAnnotations.length > 0 ? 'border-t border-indigo-500/10' : ''
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-indigo-300 uppercase tracking-wider">
                  factor recommendation
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wide ${
                    CONFIDENCE_STYLES[r.confidence] || CONFIDENCE_STYLES.LOW
                  }`}
                >
                  {r.confidence} confidence
                </span>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Recommended factor
                </span>
                <p className="text-[11px] text-slate-300 leading-relaxed">
                  {r.recommended_factor_label || 'None of the candidates were a confident match.'}
                </p>
              </div>

              <p className="text-[11px] text-slate-300 leading-relaxed">{r.explanation}</p>

              {r.reasoning && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Reasoning
                  </span>
                  <p className="text-[11px] text-indigo-200/90 leading-relaxed">{r.reasoning}</p>
                </div>
              )}

              {r.alternative_candidates?.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Alternative candidates
                  </span>
                  <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                    {r.alternative_candidates.map((candidate, i) => (
                      <li key={i}>{candidate}</li>
                    ))}
                  </ul>
                </div>
              )}

              <span className="text-[9px] text-slate-600 font-mono">
                {new Date(r.created_at).toLocaleString()}
              </span>
            </div>
          ))}

          {validationAnnotations.map((v, idx) => (
            <div
              key={v.id}
              className={`flex flex-col gap-2 pt-2 ${
                idx > 0 || anomalyAnnotations.length > 0 || factorRecommendations.length > 0
                  ? 'border-t border-indigo-500/10'
                  : ''
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold text-indigo-300 uppercase tracking-wider">
                  validation assistance
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase tracking-wide ${
                    CONFIDENCE_STYLES[v.confidence] || CONFIDENCE_STYLES.LOW
                  }`}
                >
                  {v.confidence} confidence
                </span>
              </div>

              {v.contributing_factors?.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Issue
                  </span>
                  <ul className="list-disc list-inside space-y-0.5 text-[10px] text-slate-400 pl-1">
                    {v.contributing_factors.map((field, i) => (
                      <li key={i}>{field}</li>
                    ))}
                  </ul>
                </div>
              )}

              <p className="text-[11px] text-slate-300 leading-relaxed">{v.explanation}</p>

              {v.suggested_investigation && (
                <div className="flex flex-col gap-1">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                    Suggested fix
                  </span>
                  <p className="text-[11px] text-indigo-200/90 leading-relaxed">
                    {v.suggested_investigation}
                  </p>
                </div>
              )}

              <span className="text-[9px] text-slate-600 font-mono">
                {new Date(v.created_at).toLocaleString()}
              </span>
            </div>
          ))}

          <p className="text-[9px] text-indigo-400/60 italic pt-1">
            AI-generated. Advisory only — does not alter this record&apos;s status, data, validation
            outcome, or emission factor.
          </p>
        </div>
      )}
    </div>
  );
};

export default AIInsightsPanel;
