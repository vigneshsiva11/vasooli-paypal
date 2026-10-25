export const formatCurrency = (value: number) => {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
};

export const formatPercentage = (value: number | null | undefined, fractionDigits = 1) => {
  if (value === null || value === undefined) return '0%';
  return `${value.toFixed(fractionDigits)}%`;
};

const ROOT_CAUSE_LABELS: Record<string, string> = {
  card_verification_failed: 'Card Verification Failed',
};

export const formatLabel = (key: string) => {
  if (ROOT_CAUSE_LABELS[key]) {
    return ROOT_CAUSE_LABELS[key];
  }
  return key
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

