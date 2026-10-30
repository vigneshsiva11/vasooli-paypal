import React, { useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { AgGridReact } from 'ag-grid-react';
import type { ColDef } from 'ag-grid-community';
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community';
import { Download, ShieldCheck, AlertCircle } from 'lucide-react';
import { appAgGridTheme } from '@/components/AgGridTheme';
import { useLedgerQuery, type LedgerRow } from '../api/getLedger';
import { useCurrency } from '@/context/CurrencyContext';
import { formatCurrency, formatLabel } from '@/utils/formatters';

ModuleRegistry.registerModules([AllCommunityModule]);

export const LedgerRoute: React.FC = () => {
  const { currency } = useCurrency();
  const { data: rows = [], isLoading, error, refetch } = useLedgerQuery(currency);
  const gridRef = useRef<AgGridReact<LedgerRow>>(null);

  const onExportCsv = () => {
    if (gridRef.current?.api) {
      gridRef.current.api.exportDataAsCsv({
        fileName: `vasooli-ledger-${currency}-${new Date().toISOString().slice(0, 10)}.csv`,
      });
    }
  };

  const columnDefs = useMemo<ColDef<LedgerRow>[]>(
    () => [
      {
        field: 'event_id',
        headerName: 'Event ID',
        width: 190,
        filter: true,
        sortable: true,
        pinned: 'left',
        cellRenderer: (params: any) => {
          const val = params.value;
          if (!val) return '';
          return (
            <Link
              to={`/audit-trail?event_id=${encodeURIComponent(val)}`}
              className="font-mono text-xs font-semibold text-[var(--color-accent)] hover:underline"
            >
              {val}
            </Link>
          );
        },
      },
      {
        field: 'created_at',
        headerName: 'Created At',
        width: 170,
        filter: true,
        sortable: true,
      },
      {
        field: 'customer_ref',
        headerName: 'Customer Ref',
        width: 140,
        filter: true,
        sortable: true,
      },
      {
        field: 'amount',
        headerName: 'Amount At Risk',
        width: 140,
        filter: true,
        sortable: true,
        valueFormatter: (params) => (params.value != null ? formatCurrency(params.value, currency) : ''),
      },
      {
        field: 'currency',
        headerName: 'Currency',
        width: 100,
        filter: true,
        sortable: true,
      },
      {
        field: 'status',
        headerName: 'Status',
        width: 120,
        filter: true,
        sortable: true,
        cellRenderer: (params: any) => {
          const val = String(params.value || '').toLowerCase();
          const isRec = val === 'recovered';
          return (
            <span
              className={`inline-block rounded px-2 py-0.5 text-xs font-semibold capitalize ${
                isRec
                  ? 'bg-[var(--color-status-recovered)]/15 text-[var(--color-status-recovered)]'
                  : 'bg-gray-100 text-[var(--color-text-muted)]'
              }`}
            >
              {params.value}
            </span>
          );
        },
      },
      {
        field: 'assertion',
        headerName: 'Assertion',
        width: 170,
        filter: true,
        sortable: true,
        cellRenderer: (params: any) => {
          const val = params.value;
          if (val === 'gateway_verified') {
            return (
              <span className="inline-flex items-center gap-1 rounded-full border border-[var(--color-status-recovered)]/30 bg-[var(--color-status-recovered)]/10 px-2.5 py-0.5 text-xs font-bold text-[var(--color-status-recovered)]">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-status-recovered)]" />
                Verified
              </span>
            );
          }
          if (val === 'merchant_asserted') {
            return (
              <span className="inline-flex items-center gap-1 rounded-full border border-[var(--color-status-risk)]/30 bg-[var(--color-status-risk)]/10 px-2.5 py-0.5 text-xs font-bold text-[var(--color-status-risk)]">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-status-risk)]" />
                Asserted
              </span>
            );
          }
          return (
            <span className="inline-block text-xs text-[var(--color-text-muted)]">
              {val || 'None'}
            </span>
          );
        },
      },
      {
        field: 'verified_amount',
        headerName: 'Verified Amount',
        width: 140,
        filter: true,
        sortable: true,
        valueFormatter: (params) => (params.value != null ? formatCurrency(params.value, currency) : ''),
      },
      {
        field: 'root_cause',
        headerName: 'Root Cause',
        width: 170,
        filter: true,
        sortable: true,
        valueFormatter: (params) => (params.value ? formatLabel(params.value) : 'None'),
      },
      {
        field: 'intervention',
        headerName: 'Intervention',
        width: 160,
        filter: true,
        sortable: true,
        valueFormatter: (params) => (params.value ? formatLabel(params.value) : 'None'),
      },
      {
        field: 'policy_verdict',
        headerName: 'Policy Verdict',
        width: 130,
        filter: true,
        sortable: true,
      },
      {
        field: 'gateway',
        headerName: 'Gateway',
        width: 110,
        filter: true,
        sortable: true,
      },
      {
        field: 'execution_ref',
        headerName: 'Execution Ref',
        width: 170,
        filter: true,
        sortable: true,
      },
    ],
    [currency]
  );

  const stats = useMemo(() => {
    let totalAtRisk = 0;
    let totalVerified = 0;
    let totalAsserted = 0;
    rows.forEach((r) => {
      totalAtRisk += r.amount_at_risk || r.amount || 0;
      if (r.assertion === 'gateway_verified') {
        totalVerified += r.verified_amount || 0;
      } else if (r.assertion === 'merchant_asserted') {
        totalAsserted += r.verified_amount || 0;
      }
    });
    return { totalAtRisk, totalVerified, totalAsserted, count: rows.length };
  }, [rows]);

  return (
    <div className="space-y-6 pb-12">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-[var(--color-accent)]">
            Verified vs Asserted
          </p>
          <h1 className="mt-1 text-3xl font-bold tracking-tight text-[var(--color-primary)]">
            Revenue Ledger
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">
            Sanitized event log comparing merchant-asserted versus gateway-verified recoveries.
          </p>
        </div>
        <button
          onClick={onExportCsv}
          disabled={rows.length === 0}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-xs font-semibold text-[var(--color-primary)] shadow-sm hover:border-[var(--color-accent)] disabled:opacity-50 transition"
        >
          <Download className="h-4 w-4" /> Export CSV
        </button>
      </header>

      {/* Summary Row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <div className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm">
          <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
            Total Events
          </span>
          <p className="mt-1 text-2xl font-bold text-[var(--color-primary)]">{stats.count}</p>
        </div>
        <div className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm">
          <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
            Total at Risk
          </span>
          <p className="mt-1 text-2xl font-bold text-[var(--color-primary)]">
            {formatCurrency(stats.totalAtRisk, currency)}
          </p>
        </div>
        <div className="rounded-xl border border-[var(--color-status-recovered)]/30 bg-[var(--color-status-recovered)]/5 p-4 shadow-sm">
          <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[var(--color-status-recovered)]">
            <ShieldCheck className="h-3.5 w-3.5" /> Gateway Verified
          </div>
          <p className="mt-1 text-2xl font-bold text-[var(--color-status-recovered)]">
            {formatCurrency(stats.totalVerified, currency)}
          </p>
        </div>
        <div className="rounded-xl border border-[var(--color-status-risk)]/30 bg-[var(--color-status-risk)]/5 p-4 shadow-sm">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-status-risk)]">
            Manually Asserted
          </span>
          <p className="mt-1 text-2xl font-bold text-[var(--color-primary)]">
            {formatCurrency(stats.totalAsserted, currency)}
          </p>
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-[var(--color-status-blocked)]/20 bg-[var(--color-status-blocked)]/5 p-8 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-status-blocked)]/10 text-[var(--color-status-blocked)]">
            <AlertCircle className="h-5 w-5" />
          </div>
          <h3 className="mt-3 text-base font-semibold text-[var(--color-primary)]">Unable to load ledger</h3>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            {(error as any)?.message || 'Database unavailable or failed to connect to backend.'}
          </p>
          <button
            onClick={() => refetch()}
            className="mt-4 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-[var(--color-primary)] hover:border-[var(--color-accent)]"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="h-[560px] w-full rounded-xl border border-gray-200 overflow-hidden shadow-sm bg-white">
          <AgGridReact<LedgerRow>
            ref={gridRef}
            theme={appAgGridTheme}
            rowData={rows}
            columnDefs={columnDefs}
            loading={isLoading}
            defaultColDef={{
              resizable: true,
            }}
            pagination={true}
            paginationPageSize={20}
            paginationPageSizeSelector={[20, 50, 100]}
          />
        </div>
      )}
    </div>
  );
};

export default LedgerRoute;
