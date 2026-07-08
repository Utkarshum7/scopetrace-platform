import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AIInsightsPanel } from './AIInsightsPanel';
import { apiService } from '../services/api';

// Phase 7b — contract-level tests: the panel is read-only (fetches once,
// renders exactly what the API returned), renders nothing when empty, and
// always visibly labels its content "AI Advisory".
vi.mock('../services/api', () => ({
  apiService: {
    getRecordAIAnnotations: vi.fn(),
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

describe('AIInsightsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing while loading and nothing when no annotations exist', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([]);
    const { container } = render(<AIInsightsPanel recordId="rec-1" />);

    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-1'));
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing if the fetch fails, instead of throwing', async () => {
    apiService.getRecordAIAnnotations.mockRejectedValue(new Error('network error'));
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

  it('refetches when recordId changes', async () => {
    apiService.getRecordAIAnnotations.mockResolvedValue([]);
    const { rerender } = render(<AIInsightsPanel recordId="rec-1" />);
    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-1'));

    rerender(<AIInsightsPanel recordId="rec-2" />);
    await waitFor(() => expect(apiService.getRecordAIAnnotations).toHaveBeenCalledWith('rec-2'));
    expect(apiService.getRecordAIAnnotations).toHaveBeenCalledTimes(2);
  });
});
