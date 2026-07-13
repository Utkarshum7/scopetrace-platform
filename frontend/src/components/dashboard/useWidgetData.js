import { useQuery } from '@tanstack/react-query';

/**
 * Standardized widget data hook. Wraps TanStack Query and derives a single
 * status: 'loading' | 'error' | 'empty' | 'success'.
 *
 * No local staleTime override -- the QueryClient's own default (60s, set
 * once in main.jsx) already applies to every query and was being
 * needlessly duplicated here (Phase 8, 8a.4). A caller can still override
 * per-call via `options`.
 *
 * @param {Array} key - react-query key
 * @param {Function} fetcher - async data fetcher
 * @param {{ isEmpty?: (data:any)=>boolean }} options
 */
export function useWidgetData(key, fetcher, { isEmpty, ...options } = {}) {
  const query = useQuery({ queryKey: key, queryFn: fetcher, ...options });

  let status = 'loading';
  if (query.isError) {
    status = 'error';
  } else if (query.isSuccess) {
    status = isEmpty && isEmpty(query.data) ? 'empty' : 'success';
  }

  return { status, data: query.data, error: query.error, refetch: query.refetch };
}

export default useWidgetData;
