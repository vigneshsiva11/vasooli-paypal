import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AgStudio } from 'ag-studio-react';
import type { AgAiHarnessSetup, AgStudioModule } from 'ag-studio';
import { useCurrency } from '@/context/CurrencyContext';
import { useLedgerQuery } from '@/features/ledger/api/getLedger';
import { applyStudioLicense } from '../utils/studioLicense';
import { isStudioAiEnabled } from '../utils/studioAiCheck';
import { buildStudioDashboardConfig } from '../utils/studioConfig';
import { StudioErrorBoundary } from './StudioErrorBoundary';
import { AlertCircle, ArrowLeft, RefreshCw } from 'lucide-react';

const StudioContent: React.FC = () => {
  const { currency } = useCurrency();
  const { data: rows, isLoading, isError, error, refetch } = useLedgerQuery(currency);

  const [aiModules, setAiModules] = useState<AgStudioModule[]>([]);
  const [aiHarness, setAiHarness] = useState<AgAiHarnessSetup | undefined>(undefined);

  // Apply license key if configured
  useEffect(() => {
    applyStudioLicense();
  }, []);

  // Only dynamically import and register AI when flag is explicitly "true"
  useEffect(() => {
    if (isStudioAiEnabled()) {
      import('../api/studioAi').then(({ getStudioAiSetup }) => {
        const setup = getStudioAiSetup();
        setAiModules(setup.modules);
        setAiHarness(() => setup.ai);
      });
    }
  }, []);

  const config = useMemo(() => {
    if (!rows || rows.length === 0) return null;
    return buildStudioDashboardConfig(rows, currency);
  }, [rows, currency]);

  if (isLoading) {
    return (
      <div className="flex h-96 w-full flex-col items-center justify-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--color-primary)] border-t-transparent" />
        <span className="text-sm font-medium text-[var(--color-text-muted)]">
          Loading Studio dashboard...
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto my-12 flex max-w-lg flex-col items-center rounded-xl border border-red-200 bg-red-50/50 p-8 text-center shadow-xs">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 mb-4">
          <AlertCircle className="h-6 w-6" />
        </div>
        <h2 className="text-xl font-bold text-[var(--color-primary)]">
          Failed to load ledger events
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          {error instanceof Error ? error.message : 'Unable to connect to the backend ledger service.'}
        </p>
        <div className="mt-6 flex items-center gap-4">
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-[var(--color-primary)] hover:bg-gray-50 transition"
          >
            <RefreshCw className="h-4 w-4" />
            Retry
          </button>
          <Link
            to="/ledger"
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition"
          >
            <ArrowLeft className="h-4 w-4" />
            Go to Ledger
          </Link>
        </div>
      </div>
    );
  }

  if (!rows || rows.length === 0) {
    return (
      <div className="mx-auto my-16 flex max-w-md flex-col items-center rounded-2xl border border-gray-200 bg-[var(--color-bg-surface)] p-8 text-center shadow-xs">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-100 text-[var(--color-text-muted)] mb-4">
          <RefreshCw className="h-6 w-6 text-gray-400" />
        </div>
        <h2 className="text-lg font-bold text-[var(--color-primary)]">No events yet.</h2>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          No ledger events found for {currency}. Ingest events or switch currencies to explore the dashboard.
        </p>
        <div className="mt-6 flex items-center gap-3">
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-[var(--color-primary)] hover:bg-gray-50 transition"
          >
            Refresh
          </button>
          <Link
            to="/ledger"
            className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition"
          >
            <ArrowLeft className="h-4 w-4" />
            Go to Ledger
          </Link>
        </div>
      </div>
    );
  }

  if (!config) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--color-primary)]">
            Studio
          </h1>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Interactive recovery analytics and verified vs asserted ledger workbench.
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white shadow-xs overflow-hidden">
        <AgStudio
          data={config.data}
          initialState={config.initialState}
          modules={aiModules}
          ai={aiHarness}
          style={{ height: 'calc(100vh - 180px)', minHeight: 650, width: '100%' }}
        />
      </div>
    </div>
  );
};

export const StudioRoute: React.FC = () => {
  return (
    <StudioErrorBoundary>
      <StudioContent />
    </StudioErrorBoundary>
  );
};

export default StudioRoute;

