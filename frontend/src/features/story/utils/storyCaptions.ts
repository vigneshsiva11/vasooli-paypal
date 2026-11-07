import type { RulePreviewResponse } from '@/features/rules/api/rulesApi';
import type { ScenarioAttemptResponse } from '@/features/agent/api/agentApi';

export interface RunStep {
  name: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  source: string;
  refs?: Record<string, any>;
}

export const COMPRESSED_TIME_LABEL =
  'Replay of a real sandbox run. Waiting time compressed. Sandbox, no real money.';

/**
 * Act 1 caption: strictly derived from RulePreviewResponse data fields.
 */
export function getRulePreviewCaption(preview: RulePreviewResponse): string {
  const paramCount = Object.keys(preview.proposed_parameters || {}).length;
  const sourceCount = Object.keys(preview.parameter_sources || {}).length;
  const fp = preview.prospective_fingerprint || preview.current_fingerprint || 'none';

  if (preview.valid) {
    return `Rule proposal "${preview.text}": valid with prospective fingerprint ${fp}. Parsed ${paramCount} parameters across ${sourceCount} verbatim source phrases.`;
  }

  const errCount = preview.errors?.length || 0;
  return `Rule proposal "${preview.text}": rejected with ${errCount} safety invariant violations. Baseline fingerprint ${preview.current_fingerprint} retained.`;
}

/**
 * Act 2 step caption: strictly derived from real RunStep data fields and refs.
 */
export function getReplayStepCaption(step: RunStep): string {
  const refs = step.refs || {};

  switch (step.name) {
    case 'onboard':
      return `Customer ${refs.customer_ref || 'unknown'} onboarded with source ${step.source}.`;
    case 'renewal_attempt':
      return `Renewal attempt on order ${refs.order_id || 'unknown'} yielded ${refs.capture_status || 'status'} with processor code ${refs.processor_response_code || 'unknown'}.`;
    case 'decline_ingested':
      return `Decline event ${refs.event_id || 'unknown'} ingested for order ${refs.order_id || 'unknown'} via ${step.source}.`;
    case 'pipeline':
      return `Pipeline evaluated policy: diagnosis ${refs.diagnosis_id || 'unknown'} approved with verdict ${refs.verdict || 'unknown'} (execution ${refs.execution_id || 'unknown'}).`;
    case 'clock': {
      const hours = refs.advance_hours != null ? refs.advance_hours : 0;
      return `Simulated clock advanced by ${hours} hours for cooldown.`;
    }
    case 'retry_executed':
      return `Payment retry executed on gateway ref ${refs.gateway_ref || 'unknown'} with status ${refs.capture_status || 'unknown'} (processor response code ${refs.processor_response_code || 'unknown'}).`;
    case 'verified':
      return `Outcome ${refs.outcome || 'unknown'} verified for event ${refs.event_id || 'unknown'} under verification ${refs.verification_id || 'unknown'}.`;
    default:
      return `Step ${step.name} completed with source ${step.source}.`;
  }
}

/**
 * Act 2 verification end caption: strictly derived from verified amount and outcome.
 */
export function getReplayVerificationCaption(amount: number, currency: string, outcome: string): string {
  return `Replay verified: ${amount.toFixed(2)} ${currency} recovered with outcome ${outcome}.`;
}

/**
 * Act 3 scenario attempt caption: strictly derived from ScenarioAttemptResponse fields.
 */
export function getScenarioAttemptCaption(attempt: ScenarioAttemptResponse): string {
  const checkStr = attempt.check ? ` on check "${attempt.check}"` : '';
  const verdictUpper = (attempt.verdict || attempt.outcome || 'evaluated').toUpperCase();
  return `Scenario "${attempt.name}" (${attempt.scenario_id}) executed tool "${attempt.tool}": gateway decision is ${verdictUpper} with reason "${attempt.reason}"${checkStr}.`;
}

/**
 * Helper to assert that a generated caption has no unfilled placeholders or undefined/null/NaN strings.
 */
export function hasUnfilledPlaceholder(text: string): boolean {
  if (!text || typeof text !== 'string') return true;
  if (/\bundefined\b|\bnull\b|\bNaN\b|\[object Object\]/.test(text)) return true;
  if (/\{[a-zA-Z0-9_]+\}/.test(text)) return true;
  return false;
}

