import { apiClient } from '@/api/client';

export interface WhoamiResponse {
  admin: boolean;
}

export interface ApplyRulesResponse {
  status: string;
  fingerprint: string;
  currency?: string;
  version?: number;
  [key: string]: any;
}

export interface RevertRulesResponse {
  status: string;
  active_fingerprint: string;
  currency?: string;
  [key: string]: any;
}

/**
 * Verify operator admin status via GET /rules/whoami.
 */
export async function verifyWhoami(token: string): Promise<WhoamiResponse> {
  return apiClient<WhoamiResponse>('/rules/whoami', {
    method: 'GET',
    headers: {
      'X-Admin-Token': token.trim(),
    },
  });
}

/**
 * Atomically apply the validated preview proposal via POST /rules/apply.
 */
export async function applyRulesProposal(
  token: string,
  previewId: string,
  expectedParentFingerprint: string
): Promise<ApplyRulesResponse> {
  return apiClient<ApplyRulesResponse>('/rules/apply', {
    method: 'POST',
    headers: {
      'X-Admin-Token': token.trim(),
    },
    body: JSON.stringify({
      preview_id: previewId,
      expected_parent_fingerprint: expectedParentFingerprint,
    }),
  });
}

/**
 * Revert USD rulebook to code baseline via POST /rules/revert.
 */
export async function revertRulesToBaseline(token: string): Promise<RevertRulesResponse> {
  return apiClient<RevertRulesResponse>('/rules/revert', {
    method: 'POST',
    headers: {
      'X-Admin-Token': token.trim(),
    },
  });
}

