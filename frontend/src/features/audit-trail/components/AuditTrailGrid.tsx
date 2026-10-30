import React, { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import type { ColDef } from 'ag-grid-community';
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community';
import { appAgGridTheme } from '@/components/AgGridTheme';
import type { AuditTrail } from '../api/getAuditTrail';
import { formatCurrency, formatLabel } from '@/utils/formatters';

ModuleRegistry.registerModules([AllCommunityModule]);

export interface AuditTrailGridRow {
  stage: string;
  action: string;
  verdictOrOutcome: string;
  source: string;
  time: string;
  detail: string;
}

export function flattenAuditTrailToRows(data: AuditTrail): AuditTrailGridRow[] {
  const rows: AuditTrailGridRow[] = [];

  // 1. Ingestion
  if (data.event) {
    rows.push({
      stage: '1-Ingestion',
      action: 'Event Ingested',
      verdictOrOutcome: data.event.status || 'ingested',
      source: data.event.gateway || 'api',
      time: data.event.created_at || '',
      detail: `${data.event.surface} | raw reason: ${data.event.raw_failure_reason} | customer: ${data.event.customer_ref} | amount: ${formatCurrency(data.event.amount, data.event.currency)}`,
    });
  }

  // 2. Diagnoses
  (data.diagnoses || []).forEach((diag) => {
    rows.push({
      stage: '2-Diagnosis',
      action: `Diagnose (v${diag.version})`,
      verdictOrOutcome: formatLabel(diag.root_cause),
      source: `${diag.method}${diag.llm_model ? ` (${diag.llm_model})` : ''}`,
      time: diag.diagnosed_at || '',
      detail: `Confidence: ${diag.confidence} | Recoverable: ${diag.recoverable} | Evidence: ${(diag.evidence || []).join('; ') || 'None'}`,
    });
  });

  // 3. Decisions
  (data.decisions || []).forEach((dec) => {
    rows.push({
      stage: '3-Decision',
      action: `Recommend (v${dec.version})`,
      verdictOrOutcome: formatLabel(dec.recommended_intervention),
      source: 'Rules Engine',
      time: dec.decided_at || '',
      detail: `ERV: ${formatCurrency(dec.expected_recovery_value)} (Prob: ${dec.recovery_probability}, Cost: ${formatCurrency(dec.estimated_cost)}) | ${dec.reasoning || ''}`,
    });
  });

  // 4. Policy Verdicts
  (data.policy_verdicts || []).forEach((pol) => {
    rows.push({
      stage: '4-Policy',
      action: `Evaluate (v${pol.version})`,
      verdictOrOutcome: pol.verdict.toUpperCase(),
      source: `${pol.rulebook_fingerprint} (${pol.rulebook_fingerprint_source})`,
      time: pol.evaluated_at || '',
      detail: `Reason: ${pol.reason} | Checks: ${(pol.checks_performed || []).join('; ')}`,
    });
  });

  // 5. Executions
  (data.executions || []).forEach((exec) => {
    rows.push({
      stage: '5-Execution',
      action: formatLabel(exec.intervention),
      verdictOrOutcome: exec.status,
      source: exec.gateway,
      time: exec.executed_at || '',
      detail: `Action: ${exec.action_type} | Ref: ${exec.gateway_ref || (exec as any).razorpay_payment_link_id || 'None'}${exec.failure_reason ? ` | Failure: ${exec.failure_reason}` : ''}`,
    });
  });

  // 6. Verifications
  (data.verifications || []).forEach((ver) => {
    const paypalEvent = (ver as any).paypal_event;
    rows.push({
      stage: '6-Verification',
      action: paypalEvent || ver.source,
      verdictOrOutcome: ver.outcome,
      source: ver.source,
      time: ver.verified_at || '',
      detail: `Recovered: ${formatCurrency(ver.amount_recovered)} | Assertion: ${ver.assertion}${ver.amount_mismatch ? ' (amount mismatch)' : ''}`,
    });
  });

  // 7. Promises
  (data.promises || []).forEach((prom) => {
    rows.push({
      stage: '7-Promise',
      action: 'Promise Recorded',
      verdictOrOutcome: prom.state,
      source: 'Customer',
      time: prom.promised_date || '',
      detail: `Promised ${formatCurrency(prom.promised_amount)} by ${prom.promised_date}`,
    });
  });

  return rows;
}

export const AuditTrailGrid: React.FC<{ data: AuditTrail }> = ({ data }) => {
  const rowData = useMemo(() => flattenAuditTrailToRows(data), [data]);

  const columnDefs = useMemo<ColDef<AuditTrailGridRow>[]>(
    () => [
      { field: 'stage', headerName: 'Stage', width: 140, filter: true, sortable: true },
      { field: 'action', headerName: 'Action', width: 180, filter: true, sortable: true },
      {
        field: 'verdictOrOutcome',
        headerName: 'Verdict / Outcome',
        width: 170,
        filter: true,
        sortable: true,
        cellRenderer: (params: any) => {
          const val = String(params.value || '').toUpperCase();
          const isGood = val.includes('AUTHORIZED') || val.includes('RECOVERED') || val.includes('COMPLETED');
          const isBad = val.includes('BLOCKED') || val.includes('FAILED');
          const colorClass = isGood
            ? 'bg-[var(--color-status-recovered)]/15 text-[var(--color-status-recovered)]'
            : isBad
            ? 'bg-[var(--color-status-blocked)]/15 text-[var(--color-status-blocked)]'
            : 'bg-gray-100 text-[var(--color-text-muted)]';
          return (
            <span className={`inline-block rounded px-2 py-0.5 text-xs font-bold ${colorClass}`}>
              {params.value}
            </span>
          );
        },
      },
      { field: 'source', headerName: 'Source', width: 160, filter: true, sortable: true },
      { field: 'time', headerName: 'Time', width: 180, filter: true, sortable: true },
      { field: 'detail', headerName: 'Detail', flex: 1, minWidth: 260, filter: true, sortable: true },
    ],
    []
  );

  return (
    <div className="mt-4 h-[500px] w-full rounded-xl border border-gray-200 overflow-hidden shadow-sm">
      <AgGridReact<AuditTrailGridRow>
        theme={appAgGridTheme}
        rowData={rowData}
        columnDefs={columnDefs}
        defaultColDef={{
          resizable: true,
        }}
      />
    </div>
  );
};
