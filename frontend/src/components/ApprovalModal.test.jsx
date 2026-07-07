import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApprovalModal } from './ApprovalModal';
import { apiService } from '../services/api';
import AuthContext from '../context/AuthContext';

// Phase 6h / H2 — contract-level tests for the governance workflow actions
// this modal drives: Draft -> Submit, Submitted -> Approve, Submitted ->
// Reject. These exist to catch exactly the kind of frontend/backend
// contract drift the Phase 6 architecture review found (the modal used to
// call approveRecord() unconditionally, which the Phase 6c backend rejects
// for anything but a Submitted record).
vi.mock('../services/api', () => ({
  apiService: {
    submitRecord: vi.fn(),
    approveRecord: vi.fn(),
    rejectRecord: vi.fn(),
  },
}));

const baseRecord = {
  id: 'rec-1',
  row_index: 3,
  normalized_value: '10.5',
  normalized_unit: 'L',
  scope_category: 'SCOPE_1',
  is_suspicious: false,
};

const renderModal = (record, authOverrides = {}) => {
  const onActionComplete = vi.fn();
  const onClose = vi.fn();
  render(
    <AuthContext.Provider value={{ canApprove: true, canUpload: true, ...authOverrides }}>
      <ApprovalModal isOpen record={record} onClose={onClose} onActionComplete={onActionComplete} />
    </AuthContext.Provider>
  );
  return { onActionComplete, onClose };
};

describe('ApprovalModal workflow contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiService.submitRecord.mockResolvedValue({});
    apiService.approveRecord.mockResolvedValue({});
    apiService.rejectRecord.mockResolvedValue({});
  });

  it('submits a Draft record via apiService.submitRecord, not approveRecord', async () => {
    const user = userEvent.setup();
    const { onActionComplete } = renderModal({ ...baseRecord, status: 'DRAFT' });

    await user.click(screen.getByRole('button', { name: /submit for approval/i }));

    await waitFor(() => expect(apiService.submitRecord).toHaveBeenCalledWith('rec-1', ''));
    expect(apiService.approveRecord).not.toHaveBeenCalled();
    expect(onActionComplete).toHaveBeenCalled();
  });

  it('approves a Submitted record via apiService.approveRecord', async () => {
    const user = userEvent.setup();
    const { onActionComplete } = renderModal({ ...baseRecord, status: 'SUBMITTED' });

    await user.click(screen.getByRole('button', { name: /confirm & lock/i }));

    await waitFor(() => expect(apiService.approveRecord).toHaveBeenCalledWith('rec-1', ''));
    expect(onActionComplete).toHaveBeenCalled();
  });

  it('rejects a Submitted record via apiService.rejectRecord once a reason is entered', async () => {
    const user = userEvent.setup();
    const { onActionComplete } = renderModal({ ...baseRecord, status: 'SUBMITTED' });

    const rejectButton = screen.getByRole('button', { name: /^reject$/i });
    expect(rejectButton).toBeDisabled();

    await user.type(screen.getByRole('textbox'), 'duplicate entry');
    expect(rejectButton).toBeEnabled();
    await user.click(rejectButton);

    await waitFor(() => expect(apiService.rejectRecord).toHaveBeenCalledWith('rec-1', 'duplicate entry'));
    expect(apiService.approveRecord).not.toHaveBeenCalled();
    expect(onActionComplete).toHaveBeenCalled();
  });

  it('hides the "Submit & Approve" convenience action from users without approve rights', () => {
    renderModal({ ...baseRecord, status: 'DRAFT' }, { canApprove: false });
    expect(screen.queryByRole('button', { name: /submit & approve/i })).not.toBeInTheDocument();
  });
});
