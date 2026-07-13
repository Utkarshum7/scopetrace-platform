import { useQuery } from '@tanstack/react-query';
import { apiService } from '../services/api';

// Mirrors backend UploadBatch.TERMINAL_STATUSES (apps/ingestion/models.py) —
// once a batch reaches one of these, polling stops. CANCELLED is included
// even though nothing can set it yet (Phase 5c: reserved, not implemented),
// so this hook needs no change when a future cancel feature lands.
const TERMINAL_STATUSES = new Set([
  'COMPLETED',
  'PARTIALLY_COMPLETED',
  'FAILED',
  'CANCELLED',
]);

const POLL_INTERVAL_MS = 1500;

/**
 * useBatchProgress(batchId) — polls GET /api/batches/{id}/progress/ until
 * the job reaches a terminal state, then stops.
 *
 * This is the ONLY place that knows the transport is polling. Components
 * consume { data, isTerminal, error, isLoading } and never see a fetch call
 * or an interval — swapping to a WebSocket/SSE push later (Phase 5c
 * requirement #3's whole point) means rewriting the inside of this hook,
 * never the components that call it.
 *
 * @param {string|null} batchId - pass null/undefined to disable polling
 *   entirely (e.g. before any upload has happened yet).
 */
export function useBatchProgress(batchId) {
  const query = useQuery({
    queryKey: ['batchProgress', batchId],
    queryFn: () => apiService.getBatchProgress(batchId),
    enabled: Boolean(batchId),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status && TERMINAL_STATUSES.has(status) ? false : POLL_INTERVAL_MS;
    },
    // A terminal result never goes stale — no point refetching it if the
    // component remounts.
    staleTime: (q) => {
      const status = q.state.data?.status;
      return status && TERMINAL_STATUSES.has(status) ? Infinity : 0;
    },
  });

  const status = query.data?.status;
  return {
    data: query.data,
    isTerminal: Boolean(status && TERMINAL_STATUSES.has(status)),
    error: query.error,
    isLoading: query.isLoading,
  };
}

export default useBatchProgress;
