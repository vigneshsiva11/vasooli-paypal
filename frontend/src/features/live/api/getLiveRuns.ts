import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { AuditTrail } from '@/features/audit-trail/api/getAuditTrail';

export interface DemoStep {
  name: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  source: string;
  refs?: Record<string, any>;
}

export interface DemoRun {
  run_id: string;
  scenario: string;
  customer_ref: string;
  amount: number;
  currency: string;
  advance_hours?: number;
  status: string;
  current_step?: string;
  steps: DemoStep[];
  final_status?: string;
  created_at: string;
  updated_at?: string;
}

export interface DemoRunsResponse {
  runs: DemoRun[];
  total: number;
}

export const getLiveRuns = async ({ signal }: { signal?: AbortSignal } = {}) => {
  return apiClient<DemoRunsResponse>('/demo/runs', { signal });
};

export const getReplayData = async () => {
  const [runData, auditData] = await Promise.all([
    apiClient<DemoRunsResponse>('/replay/run.json'),
    apiClient<AuditTrail>('/replay/audit-trail.json'),
  ]);
  return { runData, auditData };
};

export const useLiveRunsQuery = (isLiveActive: boolean) => {
  return useQuery({
    queryKey: ['demo', 'runs'],
    queryFn: ({ signal }) => getLiveRuns({ signal }),
    refetchInterval: isLiveActive ? 3000 : false,
    retry: 1,
  });
};

export const useTriggerPublicRunMutation = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      // Calling POST /demo/public/run (no token, ever)
      return apiClient<{ run_id: string; message: string }>('/demo/public/run', {
        method: 'POST',
        body: JSON.stringify({ amount: 49.0 }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['demo', 'runs'] });
      queryClient.invalidateQueries({ queryKey: ['ledger'] });
    },
  });
};

