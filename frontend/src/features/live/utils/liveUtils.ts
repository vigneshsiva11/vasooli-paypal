export const SOURCE_BADGE_MAP: Record<string, string> = {
  paypal: 'PayPal API',
  paypal_sandbox_simulation: 'Sandbox simulation',
  paypal_webhook: 'PayPal webhook, signature verified',
  orchestrator: 'Vasooli pipeline',
  sim_clock: 'Simulated clock',
  simulated_clock: 'Simulated clock',
};

export function getSourceBadgeText(source?: string | null): string {
  if (!source) return '';
  return SOURCE_BADGE_MAP[source] || source;
}

export interface RunStateResult {
  disabled: boolean;
  message: string;
  retryAfterSeconds: number | null;
}

export function handleRunTriggerError(error: any): RunStateResult {
  if (!error) {
    return { disabled: false, message: '', retryAfterSeconds: null };
  }
  const status = typeof error.status === 'number' ? error.status : null;

  if (status === 403) {
    return {
      disabled: true,
      message: 'Live runs disabled in this environment',
      retryAfterSeconds: null,
    };
  }
  if (status === 409) {
    return {
      disabled: false,
      message: 'A run is currently active. Please wait for it to complete.',
      retryAfterSeconds: null,
    };
  }
  if (status === 429) {
    let seconds = 60;
    if (error.payload?.retry_after) {
      seconds = Number(error.payload.retry_after);
    } else if (error.payload?.detail && typeof error.payload.detail === 'string') {
      const match = error.payload.detail.match(/(\d+)\s*(?:seconds|sec|s)/i);
      if (match) seconds = parseInt(match[1], 10);
    }
    return {
      disabled: false,
      message: `Rate limit reached. Please wait ${seconds}s before retrying.`,
      retryAfterSeconds: seconds,
    };
  }

  return {
    disabled: false,
    message: error.message || 'Unable to connect to backend server. Please check connection and try again.',
    retryAfterSeconds: null,
  };
}

export interface EvidenceItem {
  key: string;
  id: string;
  stepName: string;
}

export function extractEvidenceFromStepRefs(steps: any[] = []): EvidenceItem[] {
  const seen = new Set<string>();
  const evidence: EvidenceItem[] = [];

  for (const step of steps) {
    if (!step?.refs || typeof step.refs !== 'object') continue;
    for (const [key, val] of Object.entries(step.refs)) {
      if (typeof val === 'string' && val.trim()) {
        const isId =
          key.endsWith('_id') ||
          key.endsWith('_ref') ||
          key === 'event_id' ||
          key === 'order_id' ||
          key === 'customer_ref' ||
          key === 'diagnosis_id' ||
          key === 'decision_id' ||
          key === 'policy_verdict_id' ||
          key === 'execution_id' ||
          key === 'verification_id' ||
          key === 'gateway_ref';
        if (isId) {
          const uniqueKey = `${key}:${val}`;
          if (!seen.has(uniqueKey)) {
            seen.add(uniqueKey);
            evidence.push({
              key,
              id: val,
              stepName: step.name || 'step',
            });
          }
        }
      }
    }
  }

  return evidence;
}

export function extractDeclineEventId(steps: any[] = []): string | null {
  const declineStep = steps.find((s) => s?.name === 'decline_ingested');
  if (declineStep?.refs?.event_id) {
    return declineStep.refs.event_id;
  }
  for (const step of steps) {
    if (step?.refs?.event_id) return step.refs.event_id;
  }
  return null;
}

