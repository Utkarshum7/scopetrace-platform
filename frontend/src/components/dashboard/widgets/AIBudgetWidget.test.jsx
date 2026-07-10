import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AIBudgetWidget } from './OrgAdminWidgets';
import { apiService } from '../../../services/api';

// Phase 7g -- AIBudgetWidget (Org Admin / Auditor, CanViewAICosts). Reads
// whatever apiService.getAICosts returns, read-only.
vi.mock('../../../services/api', () => ({
  apiService: { getAICosts: vi.fn() },
}));

function renderWithQueryClient(ui) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('AIBudgetWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows utilization percentage, spend, and token consumption when AI is enabled', async () => {
    apiService.getAICosts.mockResolvedValue({
      ai_enabled: true,
      token_consumption: { input_tokens: 1000, output_tokens: 500 },
      estimated_spend_usd: '5.000000',
      budget: { spent_usd: '5.000000', budget_usd: '10.00', utilization_pct: 50.0, over_budget: false },
      provider_distribution: { echo: 3 },
      capability_distribution: { anomaly_detection: 3 },
    });
    renderWithQueryClient(<AIBudgetWidget filters={{}} />);
    expect(await screen.findByText('50%')).toBeInTheDocument();
    expect(screen.getByText(/\$5\.00 spent of \$10\.00/)).toBeInTheDocument();
  });

  it('flags over-budget organizations', async () => {
    apiService.getAICosts.mockResolvedValue({
      ai_enabled: true,
      token_consumption: { input_tokens: 0, output_tokens: 0 },
      estimated_spend_usd: '20.000000',
      budget: { spent_usd: '20.000000', budget_usd: '10.00', utilization_pct: 200.0, over_budget: true },
      provider_distribution: {},
      capability_distribution: {},
    });
    renderWithQueryClient(<AIBudgetWidget filters={{}} />);
    const value = await screen.findByText('200%');
    expect(value.className).toContain('text-danger-400');
  });

  it('shows a disabled message when AI is not enabled for the organization', async () => {
    apiService.getAICosts.mockResolvedValue({
      ai_enabled: false,
      token_consumption: { input_tokens: 0, output_tokens: 0 },
      estimated_spend_usd: '0.000000',
      budget: { spent_usd: '0.000000', budget_usd: '0.00', utilization_pct: null, over_budget: false },
      provider_distribution: {},
      capability_distribution: {},
    });
    renderWithQueryClient(<AIBudgetWidget filters={{}} />);
    expect(await screen.findByText('AI is not enabled for this organization.')).toBeInTheDocument();
  });
});
