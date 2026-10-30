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

export const deriveGatewayConfirmedText = (gateway?: string | null): string => {
  if (!gateway) return 'Gateway webhook confirmed';
  const normalized = gateway.toLowerCase();
  if (normalized === 'paypal') return 'PayPal webhook confirmed';
  if (normalized === 'razorpay') return 'Razorpay webhook confirmed';
  return `${formatLabel(gateway)} webhook confirmed`;
};
