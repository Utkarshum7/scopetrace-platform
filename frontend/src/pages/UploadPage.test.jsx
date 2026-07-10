import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Phase 7.5 (H4-6) -- targeted coverage for UploadPage's dashboard-query
// invalidation, NOT a full page test suite (UploadPage otherwise has no
// dedicated tests yet; broader coverage is separate, lower-severity debt).
// useBatchProgress is mocked directly and driven through PROCESSING ->
// terminal, since simulating its real polling/timer internals here would
// duplicate that hook's own tests for no benefit -- this file only checks
// that UploadPage reacts to isTerminal correctly.
// vi.mock() factories are hoisted above the whole module, including any
// plain `const` declarations -- vi.hoisted() is the correct way to declare
// a mock fn that both the (also-hoisted) factory and the test body need to
// share a single reference to.
const { mockUseBatchProgress } = vi.hoisted(() => ({ mockUseBatchProgress: vi.fn() }));
vi.mock('../hooks/useBatchProgress', () => ({
  useBatchProgress: (...args) => mockUseBatchProgress(...args),
}));

vi.mock('../services/api', () => ({
  apiService: {
    getDataSources: vi.fn(),
    uploadFile: vi.fn(),
  },
}));

import { apiService } from '../services/api';
import { UploadPage } from './UploadPage';

const sapDataSource = { id: 'ds-1', name: 'SAP Feed', source_type: 'SAP_FUEL' };

function renderUploadPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const makeUi = () => (
    <QueryClientProvider client={queryClient}>
      <UploadPage setView={vi.fn()} />
    </QueryClientProvider>
  );
  const { rerender } = render(makeUi());
  return { invalidateSpy, rerender: () => rerender(makeUi()) };
}

async function submitAFile(user) {
  const fileInput = document.querySelector('input[type="file"]');
  const file = new File(['a,b\n1,2'], 'sap.csv', { type: 'text/csv' });
  await user.upload(fileInput, file);
  await user.click(screen.getByRole('button', { name: /execute ingestion adaptor/i }));
}

describe('UploadPage dashboard-query invalidation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.getDataSources.mockResolvedValue([sapDataSource]);
    apiService.uploadFile.mockResolvedValue({ batch_id: 'batch-1' });
    mockUseBatchProgress.mockReturnValue({ data: null, isTerminal: false });
  });

  it('does not invalidate immediately after a successful upload response (batch not yet terminal)', async () => {
    const user = userEvent.setup();
    const { invalidateSpy } = renderUploadPage();
    await waitFor(() => expect(apiService.getDataSources).toHaveBeenCalled());

    await submitAFile(user);
    await waitFor(() => expect(apiService.uploadFile).toHaveBeenCalled());

    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('invalidates dashboard queries once the batch reaches a terminal state', async () => {
    const user = userEvent.setup();
    const { invalidateSpy, rerender } = renderUploadPage();
    await waitFor(() => expect(apiService.getDataSources).toHaveBeenCalled());

    await submitAFile(user);
    await waitFor(() => expect(apiService.uploadFile).toHaveBeenCalled());
    expect(invalidateSpy).not.toHaveBeenCalled();

    // Simulate useBatchProgress's polling reaching a terminal status, then
    // force a re-render so UploadPage picks up the new mocked return value
    // (a mocked hook has no internal state of its own to trigger this
    // automatically the way the REAL useQuery-backed hook would).
    mockUseBatchProgress.mockReturnValue({
      data: { status: 'COMPLETED' }, isTerminal: true,
    });
    rerender();

    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
  });
});
