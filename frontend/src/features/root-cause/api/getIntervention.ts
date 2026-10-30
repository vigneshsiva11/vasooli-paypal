import { useSuspenseQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

type InterventionMetrics = components['schemas']['InterventionMetrics'];

export const getIntervention = async ({ signal, currency }: { signal?: AbortSignal; currency?: string } = {}) => {
  const query = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiClient<InterventionMetrics[]>(`/metrics/by-intervention${query}`, { signal });
};

export const useInterventionQuery = (currency?: string) => {
  return useSuspenseQuery({
    queryKey: ['metrics', 'intervention', currency],
    queryFn: ({ signal }) => getIntervention({ signal, currency }),
  });
};
