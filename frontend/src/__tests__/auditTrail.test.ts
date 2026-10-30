import { describe, it, expect } from 'vitest';
import { selectDefaultEventId, getAuditTrailErrorMessage } from '../features/audit-trail/utils/auditTrailUtils';

describe('audit-trail default selection order', () => {
  const sampleLedger = [
    {
      event_id: 'evt_newest_pending',
      customer_ref: 'c1',
      created_at: '2026-10-25T12:00:00Z',
      amount: 100,
      amount_at_risk: 100,
      currency: 'USD',
      status: 'pending',
      assertion: null,
      gateway: 'paypal',
      verified_amount: 0,
    },
    {
      event_id: 'evt_verified_recovered',
      customer_ref: 'c2',
      created_at: '2026-10-24T12:00:00Z',
      amount: 50,
      amount_at_risk: 50,
      currency: 'USD',
      status: 'recovered',
      assertion: 'gateway_verified',
      gateway: 'paypal',
      verified_amount: 50,
    },
    {
      event_id: 'evt_asserted_recovered',
      customer_ref: 'c3',
      created_at: '2026-10-23T12:00:00Z',
      amount: 25,
      amount_at_risk: 25,
      currency: 'USD',
      status: 'recovered',
      assertion: 'merchant_asserted',
      gateway: 'razorpay',
      verified_amount: 25,
    },
  ];

  it('1. picks event_id from URL query if present', () => {
    const chosen = selectDefaultEventId('evt_from_url', sampleLedger as any, []);
    expect(chosen).toBe('evt_from_url');
  });

  it('2. picks newest gateway_verified recovered event from ledger if no query param', () => {
    const chosen = selectDefaultEventId(null, sampleLedger as any, []);
    expect(chosen).toBe('evt_verified_recovered');
  });

  it('3. picks newest event if no gateway_verified event exists', () => {
    const ledgerWithoutVerified = [
      {
        event_id: 'evt_newest_only',
        customer_ref: 'c1',
        created_at: '2026-10-25T12:00:00Z',
        amount: 100,
        amount_at_risk: 100,
        currency: 'USD',
        status: 'pending',
        assertion: null,
        gateway: 'paypal',
        verified_amount: 0,
      },
    ];
    const chosen = selectDefaultEventId(null, ledgerWithoutVerified as any, []);
    expect(chosen).toBe('evt_newest_only');
  });

  it('4. returns null when there are no events', () => {
    const chosen = selectDefaultEventId(null, [], []);
    expect(chosen).toBeNull();
  });
});

describe('audit-trail error messages by type', () => {
  it('404 -> "That event is not in this dataset"', () => {
    const error = { status: 404, message: 'Not found' };
    expect(getAuditTrailErrorMessage(error)).toBe('That event is not in this dataset');
  });

  it('401/403/503 -> "This action is disabled in the public demo"', () => {
    expect(getAuditTrailErrorMessage({ status: 401 })).toBe('This action is disabled in the public demo');
    expect(getAuditTrailErrorMessage({ status: 403 })).toBe('This action is disabled in the public demo');
    expect(getAuditTrailErrorMessage({ status: 503 })).toBe('This action is disabled in the public demo');
  });

  it('network failure or timeout -> "Couldn\'t reach the backend. The free server may be waking up; try again in a minute."', () => {
    const networkError = { message: 'Failed to fetch', status: null };
    expect(getAuditTrailErrorMessage(networkError)).toBe(
      "Couldn't reach the backend. The free server may be waking up; try again in a minute."
    );

    const timeoutError = { message: 'Request timeout after 5000ms' };
    expect(getAuditTrailErrorMessage(timeoutError)).toBe(
      "Couldn't reach the backend. The free server may be waking up; try again in a minute."
    );
  });
});

