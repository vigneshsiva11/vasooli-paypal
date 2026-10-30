import { useSuspenseQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

type RootCauseMetrics = components['schemas']['RootCauseMetrics'];

export const getRootCause = async ({ signal, currency }: { signal?: AbortSignal; currency?: string } = {}) => {
  const query = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiClient<RootCauseMetrics[]>(`/metrics/by-root-cause${query}`, { signal });
};

export const useRootCauseQuery = (currency?: string) => {
  return useSuspenseQuery({
    queryKey: ['metrics', 'root-cause', currency],
    queryFn: ({ signal }) => getRootCause({ signal, currency }),
  });
};
