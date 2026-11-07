import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { buildStudioDashboardConfig } from '../features/studio/utils/studioConfig';
import { mapAiError, PROMPT_STARTERS, StudioLlmAdapter } from '../features/studio/api/studioAi';
import { isStudioAiEnabled } from '../features/studio/utils/studioAiCheck';
import type { LedgerRow } from '../features/ledger/api/getLedger';
import * as clientModule from '../api/client';

describe('AG Studio Dashboard Configuration', () => {
  const sampleRows: LedgerRow[] = [
    {
      event_id: 'evt_001',
      customer_ref: 'cust_abc',
      created_at: '2026-11-06T12:00:00Z',
      amount: 2500,
      amount_at_risk: 2500,
      currency: 'USD',
      status: 'RECOVERED',
      root_cause: 'card_verification_failed',
      gateway: 'razorpay',
      verified_amount: 2500,
      assertion: 'gateway_verified',
    },
    {
      event_id: 'evt_002',
      customer_ref: 'cust_xyz',
      created_at: '2026-11-06T12:30:00Z',
      amount: 1200,
      amount_at_risk: 1200,
      currency: 'USD',
      status: 'BLOCKED',
      root_cause: 'insufficient_funds',
      gateway: 'paypal',
      verified_amount: 0,
      assertion: null,
    },
  ];

  it('builds the dashboard config from sample rows with 3 KPI cards, 1 bar chart, and 1 grid', () => {
    const config = buildStudioDashboardConfig(sampleRows, 'USD');

    expect(config).toBeDefined();
    expect(config.data.sources[0].id).toBe('ledger');
    expect(config.data.sources[0].data).toHaveLength(2);
    expect(config.data.sources[0].data[0].event_id).toBe('evt_001');

    const page = config.initialState.pages[0];
    expect(page).toBeDefined();
    expect(page.id).toBe('studio-overview');

    const widgets = (page.widgets || {}) as Record<string, any>;
    expect(widgets['kpi-verified-recovered']).toBeDefined();
    expect(widgets['kpi-verified-recovered'].type).toBe('value');
    expect(widgets['kpi-verified-recovered'].dataMapping.value[0].id).toBe('verified_amount');
    expect(widgets['kpi-verified-recovered'].dataMapping.value[0].aggregation).toBe('sum');

    expect(widgets['kpi-amount-at-risk']).toBeDefined();
    expect(widgets['kpi-amount-at-risk'].type).toBe('value');
    expect(widgets['kpi-amount-at-risk'].dataMapping.value[0].id).toBe('amount_at_risk');
    expect(widgets['kpi-amount-at-risk'].dataMapping.value[0].aggregation).toBe('sum');

    expect(widgets['kpi-event-count']).toBeDefined();
    expect(widgets['kpi-event-count'].type).toBe('value');
    expect(widgets['kpi-event-count'].dataMapping.value[0].id).toBe('event_id');
    expect(widgets['kpi-event-count'].dataMapping.value[0].aggregation).toBe('count');

    expect(widgets['chart-root-cause']).toBeDefined();
    expect(widgets['chart-root-cause'].type).toBe('bar-chart-grouped');
    expect(widgets['chart-root-cause'].dataMapping.categoryKey[0].id).toBe('root_cause');
    expect(widgets['chart-root-cause'].dataMapping.valueKey[0].id).toBe('amount_at_risk');
    expect(widgets['chart-root-cause'].dataMapping.valueKey[0].aggregation).toBe('sum');

    expect(widgets['grid-ledger-events']).toBeDefined();
    expect(widgets['grid-ledger-events'].type).toBe('grid');
    const cols = widgets['grid-ledger-events'].dataMapping.cols.map((c: any) => c.id);
    expect(cols).toContain('event_id');
    expect(cols).toContain('customer_ref');
    expect(cols).toContain('amount_at_risk');
    expect(cols).toContain('verified_amount');
    expect(cols).toContain('status');
    expect(cols).toContain('root_cause');
    expect(cols).toContain('gateway');
  });

  it('formats currency using formatCurrency in formats override', () => {
    const configUsd = buildStudioDashboardConfig(sampleRows, 'USD');
    const usdFormatter = configUsd.data.formats.currencyFormat.createValueFormatter({} as any, {} as any);
    expect(usdFormatter(2500)).toContain('2,500.00');

    const configInr = buildStudioDashboardConfig(sampleRows, 'INR');
    const inrFormatter = configInr.data.formats.currencyFormat.createValueFormatter({} as any, {} as any);
    expect(inrFormatter(2500)).toContain('2,500.00');
  });
});

describe('AG Studio Assistant & Adapter Error Mapping', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('maps 429 status to friendly message with Retry-After countdown', () => {
    expect(mapAiError(429, 30)).toBe('Rate limit reached. Please retry in 30s.');
    expect(mapAiError(429, '45')).toBe('Rate limit reached. Please retry in 45s.');
    expect(mapAiError(429, null)).toBe('Rate limit reached. Please retry in 60s.');
  });

  it('maps 503 status to required friendly message', () => {
    expect(mapAiError(503)).toBe('The assistant is switched off or busy; the dashboard still works');
  });

  it('maps network errors to friendly message', () => {
    expect(mapAiError(null, null, new TypeError('Failed to fetch'))).toBe(
      'Network error: unable to reach the AI assistant. Please check your connection.'
    );
    expect(mapAiError(undefined, null, new Error('Connection refused'))).toBe(
      'Network error: unable to reach the AI assistant. Please check your connection.'
    );
  });

  it('handles 429 streamed response in StudioLlmAdapter', async () => {
    const mockHeaders = new Headers();
    mockHeaders.set('Retry-After', '25');

    vi.spyOn(clientModule, 'streamApiClient').mockResolvedValueOnce({
      ok: false,
      status: 429,
      headers: mockHeaders,
    } as any);

    const adapter = new StudioLlmAdapter();
    const handler = adapter.executeTurn({
      input: [],
      responseFormat: { type: 'text' },
    });

    const events: any[] = [];
    for await (const ev of handler.stream) {
      events.push(ev);
    }

    const contentEvent = events.find((e) => e.type === 'TEXT_MESSAGE_CONTENT');
    expect(contentEvent).toBeDefined();
    expect(contentEvent.delta).toBe('Rate limit reached. Please retry in 25s.');

    const complete = await handler.complete;
    expect(complete.status).toBe('completed');
    expect((complete.output[0] as any).content[0].text).toBe('Rate limit reached. Please retry in 25s.');
  });

  it('handles 503 streamed response in StudioLlmAdapter', async () => {
    vi.spyOn(clientModule, 'streamApiClient').mockResolvedValueOnce({
      ok: false,
      status: 503,
      headers: new Headers(),
    } as any);

    const adapter = new StudioLlmAdapter();
    const handler = adapter.executeTurn({
      input: [],
      responseFormat: { type: 'text' },
    });

    const events: any[] = [];
    for await (const ev of handler.stream) {
      events.push(ev);
    }

    const contentEvent = events.find((e) => e.type === 'TEXT_MESSAGE_CONTENT');
    expect(contentEvent.delta).toBe('The assistant is switched off or busy; the dashboard still works');

    const complete = await handler.complete;
    expect((complete.output[0] as any).content[0].text).toBe(
      'The assistant is switched off or busy; the dashboard still works'
    );
  });

  it('handles network error in StudioLlmAdapter', async () => {
    vi.spyOn(clientModule, 'streamApiClient').mockRejectedValueOnce(new TypeError('Failed to fetch'));

    const adapter = new StudioLlmAdapter();
    const handler = adapter.executeTurn({
      input: [],
      responseFormat: { type: 'text' },
    });

    const events: any[] = [];
    for await (const ev of handler.stream) {
      events.push(ev);
    }

    const contentEvent = events.find((e) => e.type === 'TEXT_MESSAGE_CONTENT');
    expect(contentEvent.delta).toBe(
      'Network error: unable to reach the AI assistant. Please check your connection.'
    );
  });

  it('has three required prompt starters', () => {
    expect(PROMPT_STARTERS).toHaveLength(3);
    const prompts = PROMPT_STARTERS.map((p) => p.prompt);
    expect(prompts).toContain('Which root cause has the most revenue at risk?');
    expect(prompts).toContain('Show verified vs asserted recoveries');
    expect(prompts).toContain('Add a chart of recoveries by status');
  });

  it('ensures AI flag off leaves assistant disabled and unregistered', () => {
    const original = import.meta.env.VITE_STUDIO_AI;
    try {
      (import.meta.env as any).VITE_STUDIO_AI = 'false';
      expect(isStudioAiEnabled()).toBe(false);

      (import.meta.env as any).VITE_STUDIO_AI = undefined;
      expect(isStudioAiEnabled()).toBe(false);
    } finally {
      (import.meta.env as any).VITE_STUDIO_AI = original;
    }
  });
});

describe('Security scan for forbidden keys and tokens', () => {
  function scanDir(dir: string, fileList: string[] = []): string[] {
    if (!fs.existsSync(dir)) return fileList;
    const entries = fs.readdirSync(dir);
    for (const entry of entries) {
      const fullPath = path.join(dir, entry);
      const stat = fs.statSync(fullPath);
      if (stat.isDirectory()) {
        scanDir(fullPath, fileList);
      } else {
        fileList.push(fullPath);
      }
    }
    return fileList;
  }

  it('ensures nothing under src or built dist contains AIza, GEMINI, or token headers', () => {
    const srcDir = path.resolve(process.cwd(), 'src');
    const distDir = path.resolve(process.cwd(), 'dist');

    const allFiles = [...scanDir(srcDir), ...scanDir(distDir)];
    // Exclude test files from false positives
    const candidateFiles = allFiles.filter((f) => !f.includes('__tests__'));

    const forbiddenTerms = [
      'AI' + 'za',
      'GEM' + 'INI',
      'x-goog-' + 'api-key',
    ];

    const violations: { file: string; term: string; line: number }[] = [];

    candidateFiles.forEach((filePath) => {
      const content = fs.readFileSync(filePath, 'utf8');
      const lines = content.split('\n');

      lines.forEach((line, idx) => {
        forbiddenTerms.forEach((term) => {
          if (line.includes(term)) {
            violations.push({
              file: path.relative(process.cwd(), filePath).replace(/\\/g, '/'),
              term,
              line: idx + 1,
            });
          }
        });
      });
    });

    expect(violations, `Security violations detected: ${JSON.stringify(violations)}`).toEqual([]);
  });
});
