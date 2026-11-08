import { createFormats, type AgReportState } from 'ag-studio';
import type { LedgerRow } from '@/features/ledger/api/getLedger';
import { formatCurrency } from '@/utils/formatters';

export const STUDIO_SOURCE_ID = 'ledger';

export interface StudioFieldDefinition {
  id: string;
  name: string;
  dataType: 'string' | 'number' | 'date';
  format: 'textFormat' | 'currencyFormat' | 'dateFormat';
}

export interface StudioDashboardConfig {
  data: {
    sources: Array<{
      id: string;
      name: string;
      data: LedgerRow[];
      fields: StudioFieldDefinition[];
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

  const fields: StudioFieldDefinition[] = [
    { id: 'event_id', name: 'Event ID', dataType: 'string', format: 'textFormat' },
    { id: 'customer_ref', name: 'Customer Ref', dataType: 'string', format: 'textFormat' },
    { id: 'created_at', name: 'Created At', dataType: 'date', format: 'dateFormat' },
    { id: 'amount_at_risk', name: 'Amount at Risk', dataType: 'number', format: 'currencyFormat' },
    { id: 'verified_amount', name: 'Verified Recovered', dataType: 'number', format: 'currencyFormat' },
    { id: 'amount', name: 'Amount', dataType: 'number', format: 'currencyFormat' },
    { id: 'status', name: 'Status', dataType: 'string', format: 'textFormat' },
    { id: 'root_cause', name: 'Root Cause', dataType: 'string', format: 'textFormat' },
    { id: 'gateway', name: 'Gateway', dataType: 'string', format: 'textFormat' },
    { id: 'diagnosis_method', name: 'Diagnosis Method', dataType: 'string', format: 'textFormat' },
    { id: 'intervention', name: 'Intervention', dataType: 'string', format: 'textFormat' },
    { id: 'policy_verdict', name: 'Policy Verdict', dataType: 'string', format: 'textFormat' },
  ];

  const prefix = STUDIO_SOURCE_ID;

  const widgets: Record<string, any> = {
    'kpi-verified-recovered': {
      type: 'value',
      format: {
        title: { text: 'Verified Recovered', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: `${prefix}.verified_amount`, aggregation: 'sum' }],
      },
    },
    'kpi-amount-at-risk': {
      type: 'value',
      format: {
        title: { text: 'Amount at Risk', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: `${prefix}.amount_at_risk`, aggregation: 'sum' }],
      },
    },
    'kpi-event-count': {
      type: 'value',
      format: {
        title: { text: 'Event Count', enabled: true },
        style: { typography: { fontWeight: 'bold' } },
      },
      dataMapping: {
        value: [{ id: `${prefix}.event_id`, aggregation: 'count' }],
      },
    },
    'chart-root-cause': {
      type: 'bar-chart-grouped',
      format: {
        title: { text: 'Amount at Risk by Root Cause', enabled: true },
      },
      dataMapping: {
        categoryKey: [{ id: `${prefix}.root_cause` }],
        valueKey: [{ id: `${prefix}.amount_at_risk`, aggregation: 'sum' }],
      },
    },
    'grid-ledger-events': {
      type: 'grid',
      format: {
        title: { text: 'Ledger Events', enabled: true },
      },
      dataMapping: {
        cols: [
          { id: `${prefix}.event_id` },
          { id: `${prefix}.customer_ref` },
          { id: `${prefix}.created_at` },
          { id: `${prefix}.amount_at_risk` },
          { id: `${prefix}.verified_amount` },
          { id: `${prefix}.status` },
          { id: `${prefix}.root_cause` },
          { id: `${prefix}.gateway` },
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
          id: prefix,
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
