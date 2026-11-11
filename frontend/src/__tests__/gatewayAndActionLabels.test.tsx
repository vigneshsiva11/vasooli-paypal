import { describe, it, expect } from 'vitest';
import { renderToString } from 'react-dom/server';
import {
  GatewayLink,
  isForbiddenGatewayUrl,
  extractOrderId,
  GATEWAY_VERIFIED_CAPTION,
} from '../components/GatewayLink';
import {
  formatActionLabel,
  isRetryAction,
} from '../utils/formatters';
import { flattenAuditTrailToRows } from '../features/audit-trail/components/AuditTrailGrid';
import { WhyCard } from '../features/live/components/WhyCard';
import { getReplayStepCaption, type RunStep } from '../features/story/utils/storyCaptions';
import type { AuditTrail } from '../features/audit-trail/api/getAuditTrail';

describe('Rule 1: Gateway links - forbidden API hosts and masked URLs', () => {
  const forbiddenUrls = [
    'https://api.sandbox.paypal.com/v2/checkout/orders/3LK16598GX422304W',
    'https://api-m.sandbox.paypal.com/v2/checkout/orders/ORD-TEST-999',
    'https://api.paypal.com/v2/checkout/orders/LIVE-ORD-111',
    'https://www.sandbox.paypal.com/invoice/p/#[MASKED]',
    'https://api.sandbox.paypal.com/v1/invoicing/invoices/INV2-[MASKED]',
    'https://example.com/checkout?token=[MASKED]',
  ];

  const ordinaryUrls = [
    'https://www.sandbox.paypal.com/invoice/p/#INV2-ABCD-1234',
    'https://www.paypal.com/invoice/payerView/id=INV2-EFGH-5678',
    'https://dashboard.razorpay.com/app/payments/pay_123',
  ];

  describe('isForbiddenGatewayUrl predicate', () => {
    it('identifies API sandbox PayPal hosts as forbidden', () => {
      expect(isForbiddenGatewayUrl('https://api.sandbox.paypal.com/v2/checkout/orders/123')).toBe(true);
    });

    it('identifies API-M sandbox PayPal hosts as forbidden', () => {
      expect(isForbiddenGatewayUrl('https://api-m.sandbox.paypal.com/v2/checkout/orders/456')).toBe(true);
    });

    it('identifies production API PayPal hosts as forbidden', () => {
      expect(isForbiddenGatewayUrl('https://api.paypal.com/v2/checkout/orders/789')).toBe(true);
    });

    it('identifies URLs containing [MASKED] as forbidden', () => {
      expect(isForbiddenGatewayUrl('https://www.sandbox.paypal.com/invoice/p/#[MASKED]')).toBe(true);
      expect(isForbiddenGatewayUrl('https://custom.site.com/auth?[MASKED]')).toBe(true);
    });

    it('permits ordinary page links (e.g. sandbox invoice page)', () => {
      ordinaryUrls.forEach((url) => {
        expect(isForbiddenGatewayUrl(url), `Expected ordinary URL to be allowed: ${url}`).toBe(false);
      });
    });

    it('returns false for empty or non-string inputs', () => {
      expect(isForbiddenGatewayUrl(null)).toBe(false);
      expect(isForbiddenGatewayUrl(undefined)).toBe(false);
      expect(isForbiddenGatewayUrl('')).toBe(false);
    });
  });

  describe('extractOrderId helper', () => {
    it('prefers explicit gateway_ref when present', () => {
      const orderId = extractOrderId(
        'https://api.sandbox.paypal.com/v2/checkout/orders/ORD-IN-URL',
        'ORD-EXPLICIT-REF'
      );
      expect(orderId).toBe('ORD-EXPLICIT-REF');
    });

    it('extracts order ID from /orders/ path when gateway_ref is absent', () => {
      const orderId = extractOrderId(
        'https://api.sandbox.paypal.com/v2/checkout/orders/3LK16598GX422304W'
      );
      expect(orderId).toBe('3LK16598GX422304W');
    });

    it('returns empty string if neither is available', () => {
      expect(extractOrderId(null, null)).toBe('');
      expect(extractOrderId('https://example.com/no-orders-here', null)).toBe('');
    });
  });

  describe('GatewayLink component rendering', () => {
    it('NEVER renders an anchor href for api.sandbox.paypal.com, api-m.sandbox.paypal.com, api.paypal.com, or [MASKED]', () => {
      forbiddenUrls.forEach((url) => {
        const html = renderToString(
          <GatewayLink url={url} gatewayRef="3LK16598GX422304W" />
        );
        expect(html).not.toContain('<a');
        expect(html).not.toContain('href=');
        expect(html).toContain('3LK16598GX422304W');
        expect(html).toContain(GATEWAY_VERIFIED_CAPTION);
        expect(html).toContain('Copy');
      });
    });

    it('renders the PayPal order id in monospace with a copy button and the exact caption', () => {
      const html = renderToString(
        <GatewayLink
          url="https://api.sandbox.paypal.com/v2/checkout/orders/3LK16598GX422304W"
          gatewayRef="3LK16598GX422304W"
        />
      );

      // Monospace element
      expect(html).toContain('font-mono');
      expect(html).toContain('3LK16598GX422304W');
      // Copy button
      expect(html).toContain('Copy');
      expect(html).toContain('title="Copy order ID"');
      // Caption
      expect(html).toContain('Confirmed by the signed webhook in the Verification step');
    });

    it('keeps ordinary links to pages unchanged as clickable <a> elements', () => {
      const invoiceUrl = 'https://www.sandbox.paypal.com/invoice/p/#INV2-ABCD-1234';
      const html = renderToString(
        <GatewayLink url={invoiceUrl} linkText="Invoice Page" />
      );

      expect(html).toContain('<a');
      expect(html).toContain(`href="${invoiceUrl}"`);
      expect(html).toContain('Invoice Page');
      expect(html).not.toContain(GATEWAY_VERIFIED_CAPTION);
    });
  });
});

describe('Rule 2: Action labels display mapper per gateway', () => {
  describe('formatActionLabel display mapper', () => {
    it('maps retry_simulated for gateway "paypal" to "Vaulted-card retry (PayPal API)"', () => {
      expect(formatActionLabel('retry_simulated', 'paypal')).toBe(
        'Vaulted-card retry (PayPal API)'
      );
      expect(formatActionLabel('retry_simulated', 'PayPal')).toBe(
        'Vaulted-card retry (PayPal API)'
      );
    });

    it('maps any retry action for gateway "paypal" to "Vaulted-card retry (PayPal API)"', () => {
      expect(formatActionLabel('retry', 'paypal')).toBe('Vaulted-card retry (PayPal API)');
      expect(formatActionLabel('retry_executed', 'paypal')).toBe(
        'Vaulted-card retry (PayPal API)'
      );
      expect(formatActionLabel('delayed_retry', 'paypal')).toBe(
        'Vaulted-card retry (PayPal API)'
      );
    });

    it('maps retry_simulated for gateway "razorpay" to "Simulated retry"', () => {
      expect(formatActionLabel('retry_simulated', 'razorpay')).toBe('Simulated retry');
      expect(formatActionLabel('retry_executed', 'razorpay')).toBe('Simulated retry');
      expect(formatActionLabel('retry', 'razorpay')).toBe('Simulated retry');
    });

    it('leaves non-retry actions cleanly formatted without the word simulated', () => {
      expect(formatActionLabel('payment_link_generated', 'paypal')).toBe(
        'Payment Link Generated'
      );
      expect(formatActionLabel('contact_logged', 'paypal')).toBe('Contact Logged');
      expect(formatActionLabel('payment_link_generated', 'razorpay')).toBe(
        'Payment Link Generated'
      );
    });

    it('handles empty or missing actions cleanly', () => {
      expect(formatActionLabel(null, 'paypal')).toBe('');
      expect(formatActionLabel(undefined, 'paypal')).toBe('');
      expect(formatActionLabel('', 'paypal')).toBe('');
    });
  });

  describe('isRetryAction helper', () => {
    it('detects retry actions', () => {
      expect(isRetryAction('retry_simulated')).toBe(true);
      expect(isRetryAction('retry_executed')).toBe(true);
      expect(isRetryAction('retry')).toBe(true);
      expect(isRetryAction('delayed_retry')).toBe(true);
    });

    it('does not falsely detect non-retry actions', () => {
      expect(isRetryAction('payment_link_generated')).toBe(false);
      expect(isRetryAction('contact_logged')).toBe(false);
      expect(isRetryAction('onboard')).toBe(false);
      expect(isRetryAction(null)).toBe(false);
    });
  });
});

describe('Rule 1 & 2 integration across components', () => {
  const sampleAuditTrail: AuditTrail = {
    event_id: 'pp_test_123',
    assembled_at: '2026-11-01T15:00:18.289Z',
    rulebook_fingerprints: [],
    event: {
      event_id: 'pp_test_123',
      surface: 'payment',
      amount: 49.0,
      currency: 'USD',
      raw_failure_reason: 'paypal_5120',
      customer_ref: 'demo-001',
      gateway: 'paypal',
      gateway_ref: '6GH69097AN303143V',
      created_at: '2026-11-01T07:37:43.117Z',
      status: 'recovered',
    } as any,
    diagnoses: [],
    decisions: [],
    policy_verdicts: [],
    executions: [
      {
        id: 'exec_001',
        event_id: 'pp_test_123',
        policy_verdict_id: 'pol_001',
        policy_verdict_version: 1,
        intervention: 'delayed_retry',
        action_type: 'retry_simulated',
        gateway: 'paypal',
        gateway_ref: '3LK16598GX422304W',
        gateway_url: 'https://api.sandbox.paypal.com/v2/checkout/orders/3LK16598GX422304W',
        status: 'completed',
        executed_at: '2026-11-01T07:38:10.126Z',
      } as any,
    ],
    verifications: [
      {
        id: 'ver_001',
        event_id: 'pp_test_123',
        outcome: 'recovered',
        amount_recovered: 49.0,
        assertion: 'gateway_verified',
        source: 'paypal_webhook',
      } as any,
    ],
    promises: [],
    timeline: [],
    record_counts: {} as any,
    distinct_recoveries: 1,
    revenue_recovered: 49.0,
  };

  it('Audit Trail grid detail row maps retry_simulated to Vaulted-card retry (PayPal API) for PayPal', () => {
    const rows = flattenAuditTrailToRows(sampleAuditTrail);
    const execRow = rows.find((r) => r.stage === '5-Execution');
    expect(execRow).toBeDefined();
    expect(execRow?.detail).toContain('Action: Vaulted-card retry (PayPal API)');
    expect(execRow?.detail).not.toContain('retry_simulated');
    expect(execRow?.gateway_url).toBe(
      'https://api.sandbox.paypal.com/v2/checkout/orders/3LK16598GX422304W'
    );
    expect(execRow?.gateway_ref).toBe('3LK16598GX422304W');
  });

  it('Audit Trail grid detail row maps retry_simulated to Simulated retry for Razorpay', () => {
    const razorpayAudit: AuditTrail = {
      ...sampleAuditTrail,
      executions: [
        {
          ...sampleAuditTrail.executions[0],
          gateway: 'razorpay',
          action_type: 'retry_simulated',
        } as any,
      ],
    };
    const rows = flattenAuditTrailToRows(razorpayAudit);
    const execRow = rows.find((r) => r.stage === '5-Execution');
    expect(execRow?.detail).toContain('Action: Simulated retry');
  });

  it('WhyCard displays mapped action label and never links PayPal API hosts', () => {
    const html = renderToString(<WhyCard auditTrail={sampleAuditTrail} />);

    // Action label check
    expect(html).toContain('Vaulted-card retry (PayPal API)');
    expect(html).not.toContain('retry_simulated');

    // Gateway link check - never renders <a href with api.sandbox.paypal.com
    expect(html).not.toContain('<a href="https://api.sandbox.paypal.com');
    expect(html).toContain('3LK16598GX422304W');
    expect(html).toContain('Confirmed by the signed webhook in the Verification step');
    expect(html).toContain('Copy');
  });

  it('Story mode captions format retry step with Vaulted-card retry (PayPal API)', () => {
    const retryStep: RunStep = {
      name: 'retry_executed',
      status: 'completed',
      source: 'paypal',
      refs: {
        gateway_ref: '3LK16598GX422304W',
        capture_status: 'COMPLETED',
        processor_response_code: '0000',
      },
    };

    const caption = getReplayStepCaption(retryStep);
    expect(caption).toContain('Vaulted-card retry (PayPal API)');
    expect(caption).toContain('3LK16598GX422304W');
    expect(caption).toContain('COMPLETED');
    expect(caption).toContain('0000');
    // Word "simulated" must NOT appear in this retry caption
    expect(caption.toLowerCase()).not.toContain('simulated');
  });

  it('Story mode captions allow "Simulated clock" for the clock step', () => {
    const clockStep: RunStep = {
      name: 'clock',
      status: 'completed',
      source: 'simulated_clock',
      refs: { advance_hours: 72 },
    };

    const caption = getReplayStepCaption(clockStep);
    expect(caption).toContain('Simulated clock advanced by 72 hours');
  });
});
