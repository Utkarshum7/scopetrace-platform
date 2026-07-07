import { beforeEach, describe, expect, it, vi } from 'vitest';

// Phase 6h / H2 — contract-level tests: pin the exact endpoint + payload
// shape each workflow action calls, so a frontend/backend drift like the
// one found during the Phase 6 architecture review (frontend calling
// approve() directly on a Draft record, when the backend now requires
// Draft -> Submitted -> Approved) fails a test instead of shipping silently.
const postMock = vi.fn();
const getMock = vi.fn();

vi.mock('axios', () => {
  const instance = {
    post: (...args) => postMock(...args),
    get: (...args) => getMock(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
  return {
    default: {
      create: () => instance,
      post: vi.fn(),
    },
  };
});

import { apiService } from './api';

describe('apiService workflow contract', () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 'rec-1', status: 'SUBMITTED' } });
  });

  it('submitRecord POSTs to /api/records/{id}/submit/ with the given reason', async () => {
    await apiService.submitRecord('rec-1', 'ready for review');
    expect(postMock).toHaveBeenCalledWith('/api/records/rec-1/submit/', { reason: 'ready for review' });
  });

  it('submitRecord defaults reason to an empty string', async () => {
    await apiService.submitRecord('rec-1');
    expect(postMock).toHaveBeenCalledWith('/api/records/rec-1/submit/', { reason: '' });
  });

  it('approveRecord POSTs to /api/records/{id}/approve/', async () => {
    await apiService.approveRecord('rec-1', 'looks good');
    expect(postMock).toHaveBeenCalledWith('/api/records/rec-1/approve/', { reason: 'looks good' });
  });

  it('rejectRecord POSTs to /api/records/{id}/reject/ with the required reason', async () => {
    await apiService.rejectRecord('rec-1', 'duplicate entry');
    expect(postMock).toHaveBeenCalledWith('/api/records/rec-1/reject/', { reason: 'duplicate entry' });
  });
});
