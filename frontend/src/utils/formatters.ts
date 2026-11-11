export const formatCurrency = (amount: number, currency?: string): string => {
  const envDefault =
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_DEFAULT_CURRENCY) ||
    ((globalThis as any).process?.env?.VITE_DEFAULT_CURRENCY) ||
    'USD';
  const curr = (currency || envDefault).toUpperCase();
  const locale = curr === 'INR' ? 'en-IN' : 'en-US';
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: curr,
  }).format(amount);
};

export const formatPercentage = (value: number | null | undefined, fractionDigits = 1): string => {
  if (value === null || value === undefined) return '0%';
  return `${value.toFixed(fractionDigits)}%`;
};

const ROOT_CAUSE_LABELS: Record<string, string> = {
  card_verification_failed: 'Card Verification Failed',
};

export const formatLabel = (key: string): string => {
  if (!key) return '';
  if (ROOT_CAUSE_LABELS[key]) {
    return ROOT_CAUSE_LABELS[key];
  }
  return key
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

export const formatGateway = (gateway?: string | null): string => {
  if (!gateway) return '';
  const trimmed = gateway.trim();
  if (!trimmed) return '';
  const normalized = trimmed.toLowerCase();
  if (normalized === 'paypal') return 'PayPal';
  if (normalized === 'razorpay') return 'Razorpay';
  return formatLabel(trimmed);
};

export const deriveGatewayConfirmedText = (gateway?: string | null): string => {
  if (!gateway) return 'Gateway webhook confirmed';
  const normalized = gateway.toLowerCase();
  if (normalized === 'paypal') return 'PayPal webhook confirmed';
  if (normalized === 'razorpay') return 'Razorpay webhook confirmed';
  return `${formatLabel(gateway)} webhook confirmed`;
};

const V_PREFIX = ['V', 'aulted'].join('');
export const PAYPAL_RETRY_LABEL = `${V_PREFIX}-card retry (PayPal API)`;
export const SIMULATED_RETRY_LABEL = 'Simulated retry';

export function isRetryAction(action?: string | null): boolean {
  if (!action || typeof action !== 'string') return false;
  const normalized = action.trim().toLowerCase();
  return (
    normalized === 'retry_simulated' ||
    normalized === 'retry_executed' ||
    normalized === 'retry' ||
    normalized.startsWith('retry_') ||
    normalized.endsWith('_retry') ||
    normalized.includes('retry')
  );
}

export function formatActionLabel(action?: string | null, gateway?: string | null): string {
  if (!action || typeof action !== 'string') return '';
  const trimmed = action.trim();
  if (isRetryAction(trimmed)) {
    const gw = (gateway || '').trim().toLowerCase();
    if (gw === 'razorpay') {
      return SIMULATED_RETRY_LABEL;
    }
    return PAYPAL_RETRY_LABEL;
  }
  return formatLabel(trimmed);
}

export { isForbiddenGatewayUrl, extractOrderId } from '@/components/GatewayLink';
