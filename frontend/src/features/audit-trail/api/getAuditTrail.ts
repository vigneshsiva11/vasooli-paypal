import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { components } from '@/api/schema';

export type AuditTrail = components['schemas']['AuditTrail'];
export type RevenueEventRecord = components['schemas']['RevenueEventRecord'];
export type LedgerRow = components['schemas']['LedgerRow'];

export const getEvents = async ({ signal }: { signal?: AbortSignal } = {}) => {
  return apiClient<RevenueEventRecord[]>('/events', { signal });
};

export const getAuditTrail = async (eventId: string, { signal }: { signal?: AbortSignal } = {}) => {
  return apiClient<AuditTrail>(`/audit-trail/${eventId}`, { signal });
};

export const useEventsQuery = () => {
  return useQuery({
    queryKey: ['events'],
    queryFn: ({ signal }) => getEvents({ signal }),
    retry: 1,
  });
};

export const useAuditTrailQuery = (eventId: string | null) => {
  return useQuery({
    queryKey: ['audit-trail', eventId],
    queryFn: ({ signal }) => getAuditTrail(eventId!, { signal }),
    enabled: Boolean(eventId),
    retry: (failureCount, error: any) => {
      // Do not retry 404 or 401/403/503
      if (error?.status === 404 || error?.status === 401 || error?.status === 403 || error?.status === 503) {
        return false;
      }
      return failureCount < 2;
    },
  });
};
