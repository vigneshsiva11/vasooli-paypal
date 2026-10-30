import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

export type LedgerRow = components['schemas']['LedgerRow'];

export const getLedger = async ({ signal, currency }: { signal?: AbortSignal; currency?: string } = {}) => {
  const query = currency ? `?currency=${encodeURIComponent(currency)}` : '';
  return apiClient<LedgerRow[]>(`/ledger${query}`, { signal });
};

export const useLedgerQuery = (currency?: string) => {
  return useQuery({
    queryKey: ['ledger', currency],
    queryFn: ({ signal }) => getLedger({ signal, currency }),
  });
};

