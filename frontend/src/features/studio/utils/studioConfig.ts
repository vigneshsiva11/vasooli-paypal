import { createFormats, type AgReportState } from 'ag-studio';
import type { LedgerRow } from '@/features/ledger/api/getLedger';
import { formatCurrency } from '@/utils/formatters';

export interface StudioDashboardConfig {
  data: {
    sources: Array<{
      id: string;
      name: string;
      data: LedgerRow[];
      fields: Array<{
        id: string;
        name: string;
        format: 'textFormat' | 'currencyFormat' | 'dateTimeFormat';
      }>;
    }>;
    formats: ReturnType<typeof createFormats>;
  };
  initialState: AgReportState;
}

export const buildStudioDashboardConfig = (
  rows: LedgerRow[],
  currency: string = 'USD'
): StudioDashboardConfig => {
  const formats = createFormats({
    overrides: {
      currencyFormat: {
        createValueFormatter: () => (val: any) => formatCurrency(Number(val) || 0, currency),
      },
    },
  });

  const fields = [
    { id: 'event_id', name: 'Event ID', format: 'textFormat' as const },
    { id: 'customer_ref', name: 'Customer Ref', format: 'textFormat' as const },
    { id: 'created_at', name: 'Created At', format: 'dateTimeFormat' as const },
    { id: 'amount_at_risk', name: 'Amount at Risk', format: 'currencyFormat' as const },
    { id: 'verified_amount', name: 'Verified Recovered', format: 'currencyFormat' as const },
    { id: 'amount', name: 'Amount', format: 'currencyFormat' as const },
    { id: 'status', name: 'Status', format: 'textFormat' as const },
    { id: 'root_cause', name: 'Root Cause', format: 'textFormat' as const },
    { id: 'gateway', name: 'Gateway', format: 'textFormat' as const },
    { id: 'diagnosis_method', name: 'Diagnosis Method', format: 'textFormat' as const },
    { id: 'intervention', name: 'Intervention', format: 'textFormat' as const },
    { id: 'policy_verdict', name: 'Policy Verdict', format: 'textFormat' as const },
  ];

  const widgets: Record<string, any> = {
    'kpi-verified-recovered': {
      type: 'value',
      format: {
        title: { text: 'Verified Recovered', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: 'verified_amount', aggregation: 'sum' }],
      },
    },
    'kpi-amount-at-risk': {
      type: 'value',
      format: {
        title: { text: 'Amount at Risk', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: 'amount_at_risk', aggregation: 'sum' }],
      },
    },
    'kpi-event-count': {
      type: 'value',
      format: {
        title: { text: 'Event Count', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: 'event_id', aggregation: 'count' }],
      },
    },
    'chart-root-cause': {
      type: 'bar-chart-grouped',
      format: {
        title: { text: 'Amount at Risk by Root Cause', enabled: true },
      },
      dataMapping: {
        categoryKey: [{ id: 'root_cause' }],
        valueKey: [{ id: 'amount_at_risk', aggregation: 'sum' }],
      },
    },
    'grid-ledger-events': {
      type: 'grid',
      format: {
        title: { text: 'Ledger Events', enabled: true },
      },
      dataMapping: {
        cols: [
          { id: 'event_id' },
          { id: 'customer_ref' },
          { id: 'created_at' },
          { id: 'amount_at_risk' },
          { id: 'verified_amount' },
          { id: 'status' },
          { id: 'root_cause' },
          { id: 'gateway' },
        ],
      },
    },
  };

  const widgetLayout: Record<string, any> = {
    'kpi-verified-recovered': { xTrack: 0, yTrack: 0, xSpan: 8, ySpan: 6 },
    'kpi-amount-at-risk': { xTrack: 8, yTrack: 0, xSpan: 8, ySpan: 6 },
    'kpi-event-count': { xTrack: 16, yTrack: 0, xSpan: 8, ySpan: 6 },
    'chart-root-cause': { xTrack: 0, yTrack: 6, xSpan: 10, ySpan: 16 },
    'grid-ledger-events': { xTrack: 10, yTrack: 6, xSpan: 14, ySpan: 16 },
  };

  const initialState: AgReportState = {
    pages: [
      {
        id: 'studio-overview',
        widgets,
        widgetLayout,
      },
    ],
    selectedPageId: 'studio-overview',
  };

  return {
    data: {
      sources: [
        {
          id: 'ledger',
          name: 'Ledger Events',
          data: rows,
          fields,
        },
      ],
      formats,
    },
    initialState,
  };
};

