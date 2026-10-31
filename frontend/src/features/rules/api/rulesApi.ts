import { apiClient } from '@/api/client';

export interface RulePreviewResponse {
  preview_id: string;
  valid: boolean;
  text: string;
  text_hash: string;
  proposed_parameters: Record<string, number | null>;
  parameter_sources: Record<string, string>;
  errors: string[];
  diff: Record<string, { current: number | null; proposed: number | null }>;
  current_fingerprint: string;
  prospective_fingerprint: string | null;
  not_applied: Array<{ requested_rule: string; reason: string }>;
}

export interface ActiveRulebook {
  currency: string;
  fingerprint: string;
  is_active: boolean;
  provenance: string; // 'code_baseline' | 'database'
  parameters: {
    max_contacts_per_event: number;
    cooldown_hours: number;
    auto_authorize_below: number;
    never_auto_at_or_above: number;
    minimum_erv: number;
    max_retries_per_event?: number | null;
    retry_cooldown_hours?: number | null;
  };
  source_text?: string | null;
  created_at?: string | null;
}

export interface RulebookHistoryItem {
  currency: string;
  fingerprint: string;
  version?: number;
  is_active: boolean;
  provenance?: string;
  parameters: Record<string, any>;
  source_text?: string | null;
  created_at?: string | null;
  approved_by?: string | null;
  parent_fingerprint?: string | null;
}

/**
 * Preview plain English rule changes (public, rate limited).
 */
export async function getRulesPreview(text: string, signal?: AbortSignal): Promise<RulePreviewResponse> {
  return apiClient<RulePreviewResponse>('/rules/preview', {
    method: 'POST',
    signal,
    body: JSON.stringify({ text }),
  });
}

/**
 * Fetch the currently active USD rulebook and provenance.
 */
export async function getActiveRulebook(signal?: AbortSignal): Promise<ActiveRulebook> {
  return apiClient<ActiveRulebook>('/rules/active', {
    method: 'GET',
    signal,
  });
}

/**
 * Fetch chronological history of USD rulebooks.
 */
export async function getRulesHistory(signal?: AbortSignal): Promise<RulebookHistoryItem[]> {
  return apiClient<RulebookHistoryItem[]>('/rules/history', {
    method: 'GET',
    signal,
  });
}

