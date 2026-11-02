import { apiClient } from '@/api/client';

export interface Scenario {
  scenario_id: string;
  name: string;
  description: string;
  tool: string;
  expected_outcome: string;
  expected_verdict: string;
  expected_reason: string;
  expected_check: string | null;
}

export interface ScenarioAttemptResponse {
  scenario_id: string;
  name: string;
  tool: string;
  outcome: string;
  verdict: string;
  reason: string;
  check: string | null;
  checks?: string[];
  rulebook_fingerprint: string | null;
}

export interface AgentAuditEntry {
  agent_id: string;
  tool: string;
  event_or_scenario: string;
  args_hash: string;
  outcome: 'allowed' | 'blocked' | 'manual_review' | 'refused' | 'error';
  reason: string;
  check: string | null;
  verdict: string | null;
  rulebook_fingerprint: string | null;
  at: string;
  client_key_hash: string;
}

/**
 * Fetch available attack and control scenarios.
 */
export async function getScenarios(signal?: AbortSignal): Promise<Scenario[]> {
  return apiClient<Scenario[]>('/agent/scenarios', { signal });
}

/**
 * Execute an in-memory scenario through the policy gateway.
 */
export async function attemptScenario(
  scenario_id: string,
  signal?: AbortSignal
): Promise<ScenarioAttemptResponse> {
  return apiClient<ScenarioAttemptResponse>('/agent/attempt', {
    method: 'POST',
    signal,
    body: JSON.stringify({ scenario_id }),
  });
}

/**
 * Fetch recent audit trail entries for agent operations (polled).
 */
export async function getAgentAudit(
  limit: number = 20,
  signal?: AbortSignal
): Promise<AgentAuditEntry[]> {
  return apiClient<AgentAuditEntry[]>(`/agent/audit?limit=${limit}`, { signal });
}

