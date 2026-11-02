export type DecisionColorName = 'green' | 'red' | 'amber' | 'gray';

export interface DecisionColorConfig {
  colorName: DecisionColorName;
  label: string;
  badgeClass: string;
  cardBorderClass: string;
  dotClass: string;
}

/**
 * Maps gateway decisions/outcomes to the required visual color scheme:
 * - Green for allowed / authorized
 * - Red for blocked / refused
 * - Amber for manual review / requires_manual_review
 */
export function getDecisionColor(decisionOrOutcome: string | null | undefined): DecisionColorConfig {
  const norm = (decisionOrOutcome || '').toLowerCase().trim();

  if (norm === 'allowed' || norm === 'authorized') {
    return {
      colorName: 'green',
      label: 'ALLOWED',
      badgeClass: 'bg-emerald-50 text-emerald-800 border-emerald-300',
      cardBorderClass: 'border-emerald-200 bg-emerald-50/40',
      dotClass: 'bg-emerald-500',
    };
  }

  if (norm === 'blocked' || norm === 'refused' || norm === 'unauthorized') {
    return {
      colorName: 'red',
      label: 'BLOCKED',
      badgeClass: 'bg-rose-50 text-rose-800 border-rose-300',
      cardBorderClass: 'border-rose-200 bg-rose-50/40',
      dotClass: 'bg-rose-500',
    };
  }

  if (
    norm === 'manual_review' ||
    norm === 'requires_manual_review' ||
    norm === 'manual review' ||
    norm === 'manual'
  ) {
    return {
      colorName: 'amber',
      label: 'MANUAL REVIEW',
      badgeClass: 'bg-amber-50 text-amber-800 border-amber-300',
      cardBorderClass: 'border-amber-200 bg-amber-50/40',
      dotClass: 'bg-amber-500',
    };
  }

  return {
    colorName: 'gray',
    label: (decisionOrOutcome || 'UNKNOWN').toUpperCase(),
    badgeClass: 'bg-gray-100 text-gray-800 border-gray-300',
    cardBorderClass: 'border-gray-200 bg-gray-50/40',
    dotClass: 'bg-gray-400',
  };
}

/**
 * Converts a policy check name and reason into a human-readable plain English clause.
 */
export function formatPolicyClause(
  check: string | null | undefined,
  reason: string | null | undefined
): { checkTitle: string; plainEnglish: string } {
  const c = (check || '').toLowerCase().trim();
  const r = (reason || '').toLowerCase().trim();

  if (r === 'ok') {
    return {
      checkTitle: 'Policy Invariant Clearance',
      plainEnglish: 'All active policy rulebook checks passed cleanly. Recovery action authorized.',
    };
  }

  if (r === 'retry_not_advised' || c === 'decision_is_actionable') {
    return {
      checkTitle: 'Actionability Guard (decision_is_actionable)',
      plainEnglish: 'Processor response code 9500 indicates do-not-retry or suspected fraud. Automated retry is strictly refused.',
    };
  }

  if (r === 'customer_opted_out' || c === 'customer_opt_out') {
    return {
      checkTitle: 'Customer Consent Invariant (customer_opt_out)',
      plainEnglish: 'Customer has opted out of communication. Outreach is prohibited by immutable policy.',
    };
  }

  if (r === 'contact_cap_exceeded' || c === 'contact_cap') {
    return {
      checkTitle: 'Outreach Frequency Cap (contact_cap)',
      plainEnglish: 'Maximum contact threshold (3 attempts per event) has been reached. Further contact is blocked.',
    };
  }

  if (r === 'amount_never_auto' || r === 'amount_requires_approval' || c === 'amount_tier') {
    return {
      checkTitle: 'Value Authorization Ceiling (amount_tier)',
      plainEnglish: 'Transaction value exceeds autonomous policy threshold. Requires manual human authorization.',
    };
  }

  if (r === 'attack_range_scenario_refused' || c === 'attack_range_guard') {
    return {
      checkTitle: 'Adversarial Range Barrier (attack_range_guard)',
      plainEnglish: 'Identifier lies within designated attack scenario range. Financial execution refused.',
    };
  }

  if (r === 'not offered' || c === 'tool_registry') {
    return {
      checkTitle: 'Gateway Capability Registry (tool_registry)',
      plainEnglish: 'Requested administrative tool is not in the allowed agent gateway registry. Access refused.',
    };
  }

  // Fallback humanizer
  const title = c ? c.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()) : 'Policy Rule';
  const detail = r ? r.replace(/_/g, ' ') : 'Condition evaluated by policy engine';
  return {
    checkTitle: title,
    plainEnglish: `Evaluated clause: ${detail}.`,
  };
}

/**
 * Extracts Retry-After seconds from an ApiError or Response headers, defaulting to 60s.
 */
export function parseRetryAfter(error: any): number {
  if (!error) return 60;

  // Check headers if available
  const headers = error.headers;
  if (headers && typeof headers.get === 'function') {
    const raw = headers.get('retry-after') || headers.get('Retry-After');
    if (raw) {
      const parsed = parseInt(raw, 10);
      if (!Number.isNaN(parsed) && parsed > 0) return parsed;
    }
  }

  // Check error payload detail string for numbers
  const detail = error.payload?.detail || error.message || '';
  const match = detail.match(/(\d+)\s*s(?:econds?)?/i);
  if (match) {
    const parsed = parseInt(match[1], 10);
    if (!Number.isNaN(parsed) && parsed > 0) return parsed;
  }

  return 60;
}

/**
 * Formats a remaining seconds countdown into user-friendly text.
 */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${s}s`;
}

