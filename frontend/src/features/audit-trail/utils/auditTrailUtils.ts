import type { LedgerRow, RevenueEventRecord } from '../api/getAuditTrail';

export function selectDefaultEventId(
  queryParamId: string | null | undefined,
  ledgerRows: LedgerRow[] = [],
  eventRecords: RevenueEventRecord[] = []
): string | null {
  if (queryParamId && queryParamId.trim()) {
    return queryParamId.trim();
  }

  const gatewayVerifiedRow = ledgerRows.find(
    (row) =>
      row.assertion === 'gateway_verified' &&
      (row.status === 'recovered' || (row.verified_amount != null && row.verified_amount > 0))
  );
  if (gatewayVerifiedRow?.event_id) {
    return gatewayVerifiedRow.event_id;
  }

  if (ledgerRows.length > 0 && ledgerRows[0]?.event_id) {
    return ledgerRows[0].event_id;
  }
  if (eventRecords.length > 0 && eventRecords[0]?.event_id) {
    return eventRecords[0].event_id;
  }

  return null;
}

export function getAuditTrailErrorMessage(error: any): string {
  if (!error) return '';
  const status = typeof error.status === 'number' ? error.status : null;

  if (status === 404) {
    return 'That event is not in this dataset';
  }
  if (status === 401 || status === 403 || status === 503) {
    return 'This action is disabled in the public demo';
  }

  const msg = (error.message || '').toLowerCase();
  if (
    status === null ||
    status === 0 ||
    msg.includes('network') ||
    msg.includes('timeout') ||
    msg.includes('failed to fetch') ||
    msg.includes('abort') ||
    msg.includes('connection') ||
    msg.includes('waking up') ||
    msg.includes("couldn't reach")
  ) {
    return "Couldn't reach the backend. The free server may be waking up; try again in a minute.";
  }

  return "Couldn't reach the backend. The free server may be waking up; try again in a minute.";
}

