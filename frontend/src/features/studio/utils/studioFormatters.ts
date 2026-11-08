import { formatLabel } from '@/utils/formatters';

export type AssertionType = 'gateway_verified' | 'merchant_asserted' | string | null | undefined;

export interface AssertionBadgeInfo {
  label: 'Verified' | 'Asserted' | 'None';
  variant: 'verified' | 'asserted' | 'none';
  className: string;
  dotClassName: string;
}

/**
 * Humanizes root cause keys (e.g. 'insufficient_funds' -> 'Insufficient Funds').
 * Uses the existing application formatter formatLabel.
 */
export const humanizeRootCause = (key?: string | null): string => {
  if (!key) return 'Unknown';
  return formatLabel(key);
};

/**
 * Derives badge presentation data for Verified vs Asserted ledger assertions.
 */
export const getAssertionBadgeInfo = (assertion: AssertionType): AssertionBadgeInfo => {
  if (assertion === 'gateway_verified') {
    return {
      label: 'Verified',
      variant: 'verified',
      className:
        'border-[var(--color-status-recovered)]/30 bg-[var(--color-status-recovered)]/10 text-[var(--color-status-recovered)]',
      dotClassName: 'bg-[var(--color-status-recovered)]',
    };
  }
  if (assertion === 'merchant_asserted') {
    return {
      label: 'Asserted',
      variant: 'asserted',
      className:
        'border-[var(--color-status-risk)]/30 bg-[var(--color-status-risk)]/10 text-[var(--color-status-risk)]',
      dotClassName: 'bg-[var(--color-status-risk)]',
    };
  }
  return {
    label: 'None',
    variant: 'none',
    className: 'border-gray-200 bg-gray-100 text-[var(--color-text-muted)]',
    dotClassName: 'bg-gray-400',
  };
};

/**
 * Shortens long event IDs for clean tabular presentation with ellipsis in the middle.
 */
export const formatShortEventId = (eventId?: string | null, targetLen: number = 14): string => {
  if (!eventId) return '';
  if (eventId.length <= targetLen) return eventId;
  const prefixLen = Math.ceil((targetLen - 3) / 2);
  const suffixLen = Math.floor((targetLen - 3) / 2);
  return `${eventId.slice(0, prefixLen)}...${eventId.slice(-suffixLen)}`;
};

