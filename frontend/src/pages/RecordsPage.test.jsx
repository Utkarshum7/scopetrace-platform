import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Phase 7.5 (H3) -- RecordsPage is the record-approval workflow page: it
// fetches/filters/paginates the emission ledger, renders the correct
// per-status workflow action, and wires ApprovalModal's completion back into
// a refetch. ApprovalModal and AIInsightsPanel are already independently
// tested (ApprovalModal.test.jsx, AIInsightsPanel.test.jsx) -- stubbed here
// so these tests stay focused on RecordsPage's own data/wiring behavior.
vi.mock('../services/api', () => ({
  apiService: {
    getDataSources: vi.fn(),
    getBatches: vi.fn(),
    getRecords: vi.fn(),
    exportRecords: vi.fn(),
  },
}));
vi.mock('../components/ApprovalModal', () => ({
  ApprovalModal: ({ isOpen, record, onClose, onActionComplete }) =>
    isOpen ? (
      <div data-testid="approval-modal">
        <span>editing {record?.id}</span>
        <button onClick={onActionComplete}>complete-action</button>
        <button onClick={onClose}>close-modal</button>
      </div>
    ) : null,
}));
vi.mock('../components/AIInsightsPanel', () => ({
  AIInsightsPanel: () => null,
}));

import { apiService } from '../services/api';
import { RecordsPage } from './RecordsPage';
import AuthContext from '../context/AuthContext';

const record = (overrides = {}) => ({
  id: 'rec-1', row_index: 1, scope_category: 'SCOPE_1',
  normalized_value: '100.5', normalized_unit: 'L', co2e_tonnes: '2.5',
  status: 'DRAFT', is_suspicious: false, is_deleted: false, validation_errors: {},
  raw_data_payload: { unit: 'L' }, created_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

function mockRecordsResponse(items, extra = {}) {
  apiService.getRecords.mockResolvedValue({ items, count: items.length, next: null, previous: null, ...extra });
}

// Phase 8 (8c): RecordsPage now reads canViewDeletedRecords from
// AuthContext (gates the "Show deleted records" filter toggle) -- every
// render needs a provider. Defaults to false; tests that care about the
// admin-only toggle pass an override.
function renderRecordsPage(props = {}, authOverrides = {}) {
  return render(
    <AuthContext.Provider value={{ canViewDeletedRecords: false, ...authOverrides }}>
      <RecordsPage {...props} />
    </AuthContext.Provider>
  );
}

describe('RecordsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.getDataSources.mockResolvedValue([]);
    apiService.getBatches.mockResolvedValue([]);
    mockRecordsResponse([]);
  });

  it('fetches dropdown data and the record list on mount', async () => {
    mockRecordsResponse([record()]);
    renderRecordsPage();
    await waitFor(() => expect(apiService.getRecords).toHaveBeenCalled());
    expect(apiService.getDataSources).toHaveBeenCalledTimes(1);
    expect(apiService.getBatches).toHaveBeenCalledTimes(1);
  });

  it('shows a loading indicator while records are being fetched', async () => {
    let resolveFn;
    apiService.getRecords.mockReturnValue(new Promise((res) => { resolveFn = res; }));
    const { container } = renderRecordsPage();
    // Phase 8 (8a.3): the ledger's loading row now renders the shared
    // ListSkeleton primitive (pulsing placeholder bars) instead of a plain
    // "Querying..." text row, matching the loading treatment already used
    // by every dashboard widget.
    await waitFor(() => expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0));
    resolveFn({ items: [], count: 0, next: null, previous: null });
    // Let the resolution settle before the test tears down, so the state
    // update isn't left dangling outside of act().
    await screen.findByText(/no records match the active filter criteria/i);
  });

  it('renders a fetched record row with its scope, values, and CO2e', async () => {
    mockRecordsResponse([record({ scope_category: 'SCOPE_2', co2e_tonnes: '3.142' })]);
    renderRecordsPage();
    expect(await screen.findByText('SCOPE_2')).toBeInTheDocument();
    expect(screen.getByText('3.142')).toBeInTheDocument();
    expect(screen.getByText('100.50')).toBeInTheDocument();
  });

  it('shows Unresolved instead of a CO2e value when co2e_tonnes is null', async () => {
    mockRecordsResponse([record({ co2e_tonnes: null })]);
    renderRecordsPage();
    expect(await screen.findByText(/unresolved/i)).toBeInTheDocument();
  });

  it('shows the empty state when no records match', async () => {
    mockRecordsResponse([]);
    renderRecordsPage();
    expect(await screen.findByText(/no records match the active filter criteria/i)).toBeInTheDocument();
  });

  it('shows an error message with a retry action if fetching records fails', async () => {
    apiService.getRecords.mockRejectedValue(new Error('network down'));
    const user = userEvent.setup();
    renderRecordsPage();
    expect(await screen.findByText(/failed to query emission records database/i)).toBeInTheDocument();

    mockRecordsResponse([record()]);
    await user.click(screen.getByRole('button', { name: /retry/i }));
    await screen.findByText('SCOPE_1');
  });

  it('shows a "Flagged" indicator when a record is suspicious but has moved past the SUSPICIOUS status', async () => {
    mockRecordsResponse([record({ status: 'SUBMITTED', is_suspicious: true })]);
    renderRecordsPage();
    expect(await screen.findByText('Flagged')).toBeInTheDocument();
  });

  it('does not show a redundant "Flagged" indicator when status is already SUSPICIOUS', async () => {
    mockRecordsResponse([record({ status: 'SUSPICIOUS', is_suspicious: true })]);
    renderRecordsPage();
    await screen.findByText(/suspicious/i);
    expect(screen.queryByText('Flagged')).not.toBeInTheDocument();
  });

  it('shows the "Show deleted records" toggle only for roles that can view deleted records', async () => {
    renderRecordsPage();
    await waitFor(() => expect(apiService.getRecords).toHaveBeenCalled());
    expect(screen.queryByLabelText(/show deleted records/i)).not.toBeInTheDocument();
  });

  it('shows the "Show deleted records" toggle when the role permits it, and marks deleted rows', async () => {
    mockRecordsResponse([record({ is_deleted: true })]);
    renderRecordsPage({}, { canViewDeletedRecords: true });
    expect(await screen.findByLabelText(/show deleted records/i)).toBeInTheDocument();
    expect(await screen.findByText('Deleted')).toBeInTheDocument();
  });

  describe('workflow action button per status', () => {
    it.each([
      ['DRAFT', 'Submit', false],
      ['SUBMITTED', 'Review', false],
      ['REJECTED', 'Resubmit', false],
      ['APPROVED', 'Secured', true],
      ['FAILED', 'Blocked', true],
    ])('status %s renders action "%s" (disabled=%s)', async (status, label, disabled) => {
      mockRecordsResponse([record({ status })]);
      renderRecordsPage();
      const button = await screen.findByRole('button', { name: label });
      expect(button.disabled).toBe(disabled);
    });
  });

  it('clicking an actionable row action button opens ApprovalModal for that record', async () => {
    mockRecordsResponse([record({ id: 'rec-42', status: 'SUBMITTED' })]);
    const user = userEvent.setup();
    renderRecordsPage();
    await user.click(await screen.findByRole('button', { name: 'Review' }));
    expect(await screen.findByTestId('approval-modal')).toBeInTheDocument();
    expect(screen.getByText('editing rec-42')).toBeInTheDocument();
  });

  it('completing the approval action refetches the record list', async () => {
    mockRecordsResponse([record({ status: 'SUBMITTED' })]);
    const user = userEvent.setup();
    renderRecordsPage();
    await user.click(await screen.findByRole('button', { name: 'Review' }));
    await screen.findByTestId('approval-modal');

    apiService.getRecords.mockClear();
    await user.click(screen.getByText('complete-action'));
    await waitFor(() => expect(apiService.getRecords).toHaveBeenCalledTimes(1));
  });

  it('clicking a row selects it and opens the detail drawer', async () => {
    mockRecordsResponse([record({ id: 'rec-9' })]);
    const user = userEvent.setup();
    renderRecordsPage();
    await screen.findByText('SCOPE_1');
    await user.click(screen.getByText('SCOPE_1').closest('tr'));
    expect(await screen.findByText('Record Audit Metadata')).toBeInTheDocument();
  });

  it('exporting calls apiService.exportRecords with the current (cleaned) filters', async () => {
    mockRecordsResponse([]);
    const user = userEvent.setup();
    renderRecordsPage({ initialFilters: { status: 'SUBMITTED' } });
    await waitFor(() => expect(apiService.getRecords).toHaveBeenCalled());
    await user.click(screen.getByRole('button', { name: /export csv/i }));
    expect(apiService.exportRecords).toHaveBeenCalledWith({ status: 'SUBMITTED' });
  });

  describe('pagination', () => {
    it('Prev/Next are disabled with no adjacent page and hidden entirely with only one page', async () => {
      mockRecordsResponse([record()], { next: null, previous: null });
      renderRecordsPage();
      await screen.findByText('SCOPE_1');
      expect(screen.queryByRole('button', { name: /next/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /prev/i })).not.toBeInTheDocument();
    });

    it('clicking Next advances the page and refetches', async () => {
      mockRecordsResponse([record()], { next: 'http://x/?page=2', previous: null });
      const user = userEvent.setup();
      renderRecordsPage();
      const next = await screen.findByRole('button', { name: /next/i });
      expect(next.disabled).toBe(false);

      apiService.getRecords.mockClear();
      mockRecordsResponse([record()], { next: null, previous: 'http://x/?page=1' });
      await user.click(next);

      await waitFor(() => {
        const call = apiService.getRecords.mock.calls[0][0];
        expect(call.page).toBe(2);
      });
    });
  });

  it('changing a filter resets to page 1 and refetches with the new filter applied', async () => {
    mockRecordsResponse([record()], { next: 'http://x/?page=2', previous: null });
    const user = userEvent.setup();
    renderRecordsPage();
    await user.click(await screen.findByRole('button', { name: /next/i })); // now on page 2

    apiService.getRecords.mockClear();
    mockRecordsResponse([]);
    // FilterBar's <label> elements aren't htmlFor-associated to their
    // <select>s, so query by the select's `name` attribute directly.
    const statusSelect = document.querySelector('select[name="status"]');
    await user.selectOptions(statusSelect, 'APPROVED');

    await waitFor(() => {
      const call = apiService.getRecords.mock.calls.at(-1)[0];
      expect(call.page).toBe(1);
      expect(call.status).toBe('APPROVED');
    });
  });
});
