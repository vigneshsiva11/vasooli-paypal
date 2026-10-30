import { describe, it, expect } from 'vitest';
import { getSourceBadgeText } from '../features/live/utils/liveUtils';

describe('source badge mapping', () => {
  it('maps known sources correctly', () => {
    expect(getSourceBadgeText('paypal')).toBe('PayPal API');
    expect(getSourceBadgeText('paypal_sandbox_simulation')).toBe('Sandbox simulation');
    expect(getSourceBadgeText('paypal_webhook')).toBe('PayPal webhook, signature verified');
    expect(getSourceBadgeText('orchestrator')).toBe('Vasooli pipeline');
    expect(getSourceBadgeText('sim_clock')).toBe('Simulated clock');
    expect(getSourceBadgeText('simulated_clock')).toBe('Simulated clock');
  });

  it('shows raw text for an unknown source, never an invented label', () => {
    expect(getSourceBadgeText('custom_source_xyz')).toBe('custom_source_xyz');
    expect(getSourceBadgeText('stripe_direct')).toBe('stripe_direct');
  });

  it('handles null/undefined gracefully', () => {
    expect(getSourceBadgeText(null)).toBe('');
    expect(getSourceBadgeText(undefined)).toBe('');
  });
});
