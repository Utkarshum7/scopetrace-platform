import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReportsWidget } from './CommonWidgets';
import { apiService } from '../../../services/api';

// Phase 7f — contract-level tests: ReportsWidget's AI narrative
// sub-section is read-only (fetches whatever the API returns), only
// appears once a date range is selected, and always visibly labels its
// content "AI Advisory". Regenerate only queues work -- it's the one
// mutating action, and it never claims a synchronous result.
vi.mock('../../../services/api', () => ({
  apiService: {
    exportRecords: vi.fn(),
    listReportNarrations: vi.fn(),
    regenerateReportNarration: vi.fn(),
  },
}));

const narration = {
  id: 'narration-1',
  date_from: '2026-01-01',
  date_to: '2026-03-31',
  scope: '',
  executive_summary: 'Total emissions for Q1 were 512.30 tCO2e.',
  key_highlights: ['Emissions declined for five consecutive months'],
  trend_explanations: 'Monthly emissions fell steadily across the period.',
  recommendations: ['Investigate the drivers behind the sustained decline.'],
  confidence: 'HIGH',
  created_at: '2026-04-01T12:00:00Z',
};

function renderWithQueryClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('ReportsWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.listReportNarrations.mockResolvedValue([]);
  });

  it('always shows the CSV export action', async () => {
    renderWithQueryClient(<ReportsWidget filters={{}} />);
    expect(screen.getByRole('button', { name: /export csv/i })).toBeInTheDocument();
  });

  it('does not show the AI narrative section when no date range is selected', async () => {
    renderWithQueryClient(<ReportsWidget filters={{}} />);
    await waitFor(() => expect(apiService.listReportNarrations).not.toHaveBeenCalled());
    expect(screen.queryByText('AI Advisory')).not.toBeInTheDocument();
  });

  it('shows the executive summary, key highlights, trend, and recommendations, clearly labeled AI Advisory', async () => {
    apiService.listReportNarrations.mockResolvedValue([narration]);
    renderWithQueryClient(
      <ReportsWidget filters={{ date_from: '2026-01-01', date_to: '2026-03-31' }} />,
    );

    expect(await screen.findByText('AI Advisory')).toBeInTheDocument();
    expect(await screen.findByText(narration.executive_summary)).toBeInTheDocument();
    expect(screen.getByText(/HIGH confidence/i)).toBeInTheDocument();
    expect(screen.getByText('Emissions declined for five consecutive months')).toBeInTheDocument();
    expect(screen.getByText(narration.trend_explanations)).toBeInTheDocument();
    expect(screen.getByText('Investigate the drivers behind the sustained decline.')).toBeInTheDocument();
  });

  it('shows a hint to regenerate when no narrative exists yet for the period', async () => {
    apiService.listReportNarrations.mockResolvedValue([]);
    renderWithQueryClient(
      <ReportsWidget filters={{ date_from: '2026-01-01', date_to: '2026-03-31' }} />,
    );
    expect(await screen.findByText(/no narrative yet/i)).toBeInTheDocument();
  });

  it('regenerate queues generation and never claims a synchronous result', async () => {
    apiService.listReportNarrations.mockResolvedValue([]);
    apiService.regenerateReportNarration.mockResolvedValue({ detail: 'Report narration generation queued.' });
    const user = userEvent.setup();
    renderWithQueryClient(
      <ReportsWidget filters={{ date_from: '2026-01-01', date_to: '2026-03-31', scope: 'SCOPE_1' }} />,
    );

    await user.click(await screen.findByRole('button', { name: /regenerate/i }));
    expect(apiService.regenerateReportNarration).toHaveBeenCalledWith({
      date_from: '2026-01-01', date_to: '2026-03-31', scope: 'SCOPE_1',
    });
    // No narrative content appears just from clicking regenerate -- the
    // API only returns a queued acknowledgement, never the narration
    // itself.
    expect(screen.queryByText(narration.executive_summary)).not.toBeInTheDocument();
  });

  it('does not render the AI section at all if the request is forbidden (e.g. Viewer role)', async () => {
    apiService.listReportNarrations.mockRejectedValue({ response: { status: 403 } });
    renderWithQueryClient(
      <ReportsWidget filters={{ date_from: '2026-01-01', date_to: '2026-03-31' }} />,
    );
    await waitFor(() => expect(apiService.listReportNarrations).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText('AI Advisory')).not.toBeInTheDocument());
  });
});
