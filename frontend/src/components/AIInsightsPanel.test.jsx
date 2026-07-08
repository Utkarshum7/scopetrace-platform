import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AIInsightsPanel } from './AIInsightsPanel';
import { apiService } from '../services/api';

// Phase 7b — contract-level tests: the panel is read-only (fetches once,
// renders exactly what the API returned), renders nothing when empty, and
// always visibly labels its content "AI Advisory". Phase 7c extends this
// with a second, independent data source (factor recommendations) --
// tests below give both mocks an empty-array default so each existing 7b
// test only needs to care about the annotations mock.
vi.mock('../services/api', () => ({
  apiService: {
    getRecordAIAnnotations: vi.fn(),
    getRecordFactorRecommendations: vi.fn(),
  },
}));

const annotation = {
  id: 'annotation-1',
  capability: 'ANOMALY_DETECTION',
  explanation: 'Quantity is far above the batch median.',
  contributing_factors: ['bulk purchase', 'possible data-entry error'],
  confidence: 'HIGH',
  suggested_investigation: "Confirm with the site's fuel log.",
  created_at: '2026-07-08T12:00:00Z',
};

const factorRecommendation = {
  id: 'factor-rec-1',
  recommended_factor_label: 'DEFRA 2024 (GLOBAL) — 2.6800 kgCO2e/L',
  confidence: 'HIGH',
  explanation: 'candidate_1 is the best regional and date match.',
  reasoning: "candidate_1's validity window covers the reporting date.",
  alternative_candidates: ['candidate_2'],
  created_at: '2026-07-08T12:00:00Z',
};

const validationAnnotation = {
  id: 'validation-1',
  capability: 'VALIDATION_ASSISTANCE',
  explanation: 'The quantity is negative, likely a sign error.',
  contributing_factors: ['quantity'],
  confidence: 'MEDIUM',
  suggested_investigation: 'Re-enter the row with the correct sign.',
  created_at: '2026-07-08T12:00:00Z',
};

describe('AIInsightsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.getRecordAIAnnotations.mockResolvedValue([]);
    apiService.getRecordFactorRecommendations.mockResolvedValue([]);
  });

  it('renders nothing while loading and nothing when neither source has data', async () => {
    const { container } = render(<AIInsightsPanel recordId="rec-1" />);

    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-1'));
    await waitFor(() => expect(apiService.getRecordFactorRecommendations).toHaveBeenCalledWith('rec-1'));
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing if both fetches fail, instead of throwing', async () => {
    apiService.getRecordAIAnnotations.mockRejectedValue(new Error('network error'));
    apiService.getRecordFactorRecommendations.mockRejectedValue(new Error('network error'));
    const { container } = render(<AIInsightsPanel recordId="rec-1" />);

    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the explanation, confidence, evidence, and recommendation, clearly labeled AI Advisory', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([annotation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
    expect(screen.getByText(annotation.explanation)).toBeInTheDocument();
    expect(screen.getByText(/HIGH confidence/i)).toBeInTheDocument();
    expect(screen.getByText('bulk purchase')).toBeInTheDocument();
    expect(screen.getByText(annotation.suggested_investigation)).toBeInTheDocument();
  });

  it('is collapsible via the header toggle', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([annotation]);
    const user = userEvent.setup();
    render(<AIInsightsPanel recordId="rec-1" />);

    const explanation = await screen.findByText(annotation.explanation);
    expect(explanation).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /AI Insights/i }));
    expect(screen.queryByText(annotation.explanation)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /AI Insights/i }));
    expect(await screen.findByText(annotation.explanation)).toBeInTheDocument();
  });

  it('refetches both sources when recordId changes', async () => {
    const { rerender } = render(<AIInsightsPanel recordId="rec-1" />);
    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-1'));

    rerender(<AIInsightsPanel recordId="rec-2" />);
    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-2'));
    expect(apiService.getRecordAIAnnotations).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(apiService.getRecordFactorRecommendations).toHaveBeenCalledWith('rec-2'));
    expect(apiService.getRecordFactorRecommendations).toHaveBeenCalledTimes(2);
  });

  it('shows the recommended factor, confidence, explanation, reasoning, and alternative candidates, clearly labeled AI Advisory', async () => {
    apiService.getRecordFactorRecommendations.mockResolvedValue([factorRecommendation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
    expect(screen.getByText(factorRecommendation.recommended_factor_label)).toBeInTheDocument();
    expect(screen.getByText(/HIGH confidence/i)).toBeInTheDocument();
    expect(screen.getByText(factorRecommendation.explanation)).toBeInTheDocument();
    expect(screen.getByText(factorRecommendation.reasoning)).toBeInTheDocument();
    expect(screen.getByText('candidate_2')).toBeInTheDocument();
  });

  it('shows a fallback message when the AI recommended none of the candidates', async () => {
    apiService.getRecordFactorRecommendations.mockResolvedValue([
      { ...factorRecommendation, recommended_factor_label: null, alternative_candidates: [] },
    ]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText(/none of the candidates/i)).toBeInTheDocument();
  });

  it('renders the panel when only factor recommendations exist and annotations are empty', async () => {
    apiService.getRecordFactorRecommendations.mockResolvedValue([factorRecommendation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
  });

  it('renders both sub-sections when both annotations and factor recommendations exist', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([annotation]);
    apiService.getRecordFactorRecommendations.mockResolvedValue([factorRecommendation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText(annotation.explanation)).toBeInTheDocument();
    expect(screen.getByText(factorRecommendation.explanation)).toBeInTheDocument();
  });

  it('shows the issue, explanation, suggested fix, and confidence for validation assistance, clearly labeled AI Advisory', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([validationAnnotation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
    expect(screen.getByText('quantity')).toBeInTheDocument();
    expect(screen.getByText(validationAnnotation.explanation)).toBeInTheDocument();
    expect(screen.getByText(/MEDIUM confidence/i)).toBeInTheDocument();
    expect(screen.getByText(validationAnnotation.suggested_investigation)).toBeInTheDocument();
  });

  it('splits anomaly detection and validation assistance into separate sections from the same endpoint', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([annotation, validationAnnotation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText(annotation.explanation)).toBeInTheDocument();
    expect(screen.getByText(validationAnnotation.explanation)).toBeInTheDocument();
    expect(screen.getByText('bulk purchase')).toBeInTheDocument();
    expect(screen.getByText('quantity')).toBeInTheDocument();
    expect(screen.getByText(annotation.suggested_investigation)).toBeInTheDocument();
    expect(screen.getByText(validationAnnotation.suggested_investigation)).toBeInTheDocument();
  });

  it('renders the panel when only validation assistance exists and everything else is empty', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([validationAnnotation]);
    render(<AIInsightsPanel recordId="rec-1" />);

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
  });
});
