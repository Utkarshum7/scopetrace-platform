import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  AIUsageWidget,
  AIProviderMixWidget,
  AIEvaluationWidget,
  AILatencyTrendWidget,
} from './AIObservabilityWidgets';
import { apiService } from '../../../services/api';

// Phase 7g -- contract-level tests for the Platform Admin AI observability
// widgets: each renders whatever apiService.getAIObservability returns,
// read-only, no mutation actions anywhere in this file.
vi.mock('../../../services/api', () => ({
  apiService: { getAIObservability: vi.fn() },
}));

const summary = {
  requests: { total: 42, by_outcome: { OK: 40, SCHEMA_INVALID: 2 }, failed: 2 },
  latency: { avg_ms: 123.4, trend: [{ date: '2026-01-01', avg_ms: 100 }, { date: '2026-01-02', avg_ms: 150 }] },
  provider_usage: { echo: 30, replay: 12 },
  replay_usage: 12,
  capability_usage: { anomaly_detection: 42 },
  tokens_and_cost: { input_tokens: 1000, output_tokens: 500, estimated_cost_usd: '0.010000' },
  cache_hits: 5,
  evaluation: {
    latest_by_tier: {
      TIER_1_DETERMINISTIC: { id: 'run-1', status: 'COMPLETED', total_cases: 5, passed_cases: 5, failed_cases: 0 },
      TIER_2_ADVISORY: null,
    },
    recent_runs: [],
    recent_outcome_breakdown: {},
    regressions: 1,
    schema_failures: 2,
    replay_failures: 0,
    invariant_suite: { note: 'ci gate' },
  },
};

function renderWithQueryClient(ui) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('AI observability widgets', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.getAIObservability.mockResolvedValue(summary);
  });

  it('AIUsageWidget shows request totals, failures, cache hits, replay usage', async () => {
    renderWithQueryClient(<AIUsageWidget filters={{}} />);
    expect(await screen.findByText('42')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('AIProviderMixWidget renders the provider chart when data exists', async () => {
    renderWithQueryClient(<AIProviderMixWidget filters={{}} />);
    await screen.findByText('Provider Mix');
    expect(screen.queryByText('No AI requests yet.')).not.toBeInTheDocument();
  });

  it('AIProviderMixWidget shows an empty message when there is no provider usage', async () => {
    apiService.getAIObservability.mockResolvedValue({ ...summary, provider_usage: {} });
    renderWithQueryClient(<AIProviderMixWidget filters={{}} />);
    expect(await screen.findByText('No AI requests yet.')).toBeInTheDocument();
  });

  it('AIEvaluationWidget shows regression/schema/replay failure counts and tier status', async () => {
    renderWithQueryClient(<AIEvaluationWidget filters={{}} />);
    expect(await screen.findByText('5/5 passed')).toBeInTheDocument();
    expect(screen.getByText('never run')).toBeInTheDocument();
  });

  it('AILatencyTrendWidget renders the trend chart when data exists', async () => {
    renderWithQueryClient(<AILatencyTrendWidget filters={{}} />);
    await screen.findByText('Latency Trend');
    expect(screen.queryByText('No latency data yet.')).not.toBeInTheDocument();
  });

  it('AILatencyTrendWidget shows an empty message when there is no trend data', async () => {
    apiService.getAIObservability.mockResolvedValue({ ...summary, latency: { avg_ms: null, trend: [] } });
    renderWithQueryClient(<AILatencyTrendWidget filters={{}} />);
    expect(await screen.findByText('No latency data yet.')).toBeInTheDocument();
  });
});
