import { useSuspenseQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

type MetricsSummary = components['schemas']['MetricsSummary'];

export const getSummary = async ({ signal, currency }: { signal?: AbortSignal; currency?: string } = {}) => {
  const query = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiClient<MetricsSummary>(`/metrics/summary${query}`, { signal });
};

export const useSummaryQuery = (currency?: string) => {
  return useSuspenseQuery({
    queryKey: ['metrics', 'summary', currency],
    queryFn: ({ signal }) => getSummary({ signal, currency }),
  });
};
