import { useQuery } from '@tanstack/react-query';

/**
 * Standardized widget data hook. Wraps TanStack Query and derives a single
 * status: 'loading' | 'error' | 'empty' | 'success'.
 *
 * @param {Array} key - react-query key
 * @param {Function} fetcher - async data fetcher
 * @param {{ isEmpty?: (data:any)=>boolean }} options
 */
export function useWidgetData(key, fetcher, { isEmpty, ...options } = {}) {
  const query = useQuery({ queryKey: key, queryFn: fetcher, staleTime: 60_000, ...options });

  let status = 'loading';
  if (query.isError) {
    status = 'error';
  } else if (query.isSuccess) {
    status = isEmpty && isEmpty(query.data) ? 'empty' : 'success';
  }

  return { status, data: query.data, error: query.error, refetch: query.refetch };
}

export default useWidgetData;
