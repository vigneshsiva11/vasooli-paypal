import { describe, it, expect } from 'vitest';
import { formatCurrency, deriveGatewayConfirmedText, formatGateway } from '../utils/formatters';

describe('formatCurrency', () => {
  it('formats USD with en-US locale by default or explicit', () => {
    const resUsd = formatCurrency(100, 'USD');
    expect(resUsd).toContain('$');
    expect(resUsd).toBe('$100.00');
  });

  it('formats INR with en-IN locale', () => {
    const resInr = formatCurrency(5000, 'INR');
    expect(resInr).toContain('₹');
    expect(resInr).toContain('5,000.00');
  });

  it('defaults to VITE_DEFAULT_CURRENCY or USD when currency not passed', () => {
    const res = formatCurrency(49);
    expect(res).toContain('$');
    expect(res).toBe('$49.00');
  });

  it('handles decimals properly', () => {
    expect(formatCurrency(17.15, 'USD')).toBe('$17.15');
    expect(formatCurrency(0, 'USD')).toBe('$0.00');
  });
});

describe('deriveGatewayConfirmedText', () => {
  it('returns PayPal webhook confirmed for paypal', () => {
    expect(deriveGatewayConfirmedText('paypal')).toBe('PayPal webhook confirmed');
    expect(deriveGatewayConfirmedText('PayPal')).toBe('PayPal webhook confirmed');
  });

  it('returns Razorpay webhook confirmed for razorpay', () => {
    expect(deriveGatewayConfirmedText('razorpay')).toBe('Razorpay webhook confirmed');
  });

  it('falls back to Gateway webhook confirmed when unknown or missing', () => {
    expect(deriveGatewayConfirmedText(null)).toBe('Gateway webhook confirmed');
    expect(deriveGatewayConfirmedText(undefined)).toBe('Gateway webhook confirmed');
  });
});

describe('formatGateway (Ledger row gateway field and verdict formatting)', () => {
  it('maps paypal to "PayPal"', () => {
    expect(formatGateway('paypal')).toBe('PayPal');
    expect(formatGateway('PayPal')).toBe('PayPal');
    expect(formatGateway(' PAYPAL ')).toBe('PayPal');
  });

  it('maps razorpay to "Razorpay"', () => {
    expect(formatGateway('razorpay')).toBe('Razorpay');
    expect(formatGateway('Razorpay')).toBe('Razorpay');
    expect(formatGateway(' RAZORPAY ')).toBe('Razorpay');
  });

  it('removes any Razorpay default when gateway is missing or null', () => {
    expect(formatGateway(null)).toBe('');
    expect(formatGateway(undefined)).toBe('');
    expect(formatGateway('')).toBe('');
    expect(formatGateway('   ')).toBe('');
  });

  it('derives label dynamically from row gateway without hard-coding', () => {
    const rowPayPal = { gateway: 'paypal' };
    const rowRazorpay = { gateway: 'razorpay' };
    const rowUnknown = { gateway: null };

    expect(formatGateway(rowPayPal.gateway)).toBe('PayPal');
    expect(formatGateway(rowRazorpay.gateway)).toBe('Razorpay');
    expect(formatGateway(rowUnknown.gateway)).not.toBe('Razorpay');
    expect(formatGateway(rowUnknown.gateway)).toBe('');
  });
});
