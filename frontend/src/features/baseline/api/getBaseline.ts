import { useSuspenseQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

type BaselineComparison = components['schemas']['BaselineComparison'];

export const getBaseline = async ({ signal, currency }: { signal?: AbortSignal; currency?: string } = {}) => {
  const query = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiClient<BaselineComparison>(`/metrics/baseline-comparison${query}`, { signal });
};

export const useBaselineQuery = (currency?: string) => {
  return useSuspenseQuery({
    queryKey: ['metrics', 'baseline', currency],
    queryFn: ({ signal }) => getBaseline({ signal, currency }),
  });
};
