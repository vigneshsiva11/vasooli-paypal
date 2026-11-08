import React, { useEffect, useMemo } from 'react';
import { useCurrency } from '@/context/CurrencyContext';
import { useLedgerQuery } from '@/features/ledger/api/getLedger';
import { applyStudioLicense } from '../utils/studioLicense';
import { isStudioAiEnabled } from '../utils/studioAiCheck';
import { buildStudioDashboardConfig } from '../utils/studioConfig';
import { StudioErrorBoundary } from './StudioErrorBoundary';
import { StudioHeader } from './StudioHeader';
import { StudioKpiCards } from './StudioKpiCards';
import { StudioRootCauseChart, type RootCauseItem } from './StudioRootCauseChart';
import { StudioGrid } from './StudioGrid';
import { StudioSkeleton } from './StudioSkeleton';
import { StudioEmptyState } from './StudioEmptyState';
import { StudioErrorCard } from './StudioErrorCard';
import { StudioAssistantPanel } from './StudioAssistantPanel';
import { StudioFooter } from './StudioFooter';

const StudioContent: React.FC = () => {
  const { currency } = useCurrency();
  const { data: rows, isLoading, isError, error, refetch, isFetching } = useLedgerQuery(currency);

  // Apply license key if configured
  useEffect(() => {
    applyStudioLicense();
  }, []);

  // Maintain Studio report configuration to guarantee schema & dataMapping invariants
  useMemo(() => {
    if (!rows || rows.length === 0) return null;
    return buildStudioDashboardConfig(rows, currency);
  }, [rows, currency]);

  // Derived KPI metrics matching the dataMapping definitions
  const kpiStats = useMemo(() => {
    if (!rows || rows.length === 0) {
      return { verifiedRecovered: 0, amountAtRisk: 0, eventsCount: 0 };
    }
    let verifiedRecovered = 0;
    let amountAtRisk = 0;
    for (const row of rows) {
      verifiedRecovered += row.verified_amount || 0;
      amountAtRisk += row.amount_at_risk || 0;
    }
    return {
      verifiedRecovered,
      amountAtRisk,
      eventsCount: rows.length,
    };
  }, [rows]);

  // Derived Root Cause metrics matching the bar-chart-grouped dataMapping
  const rootCauseData = useMemo<RootCauseItem[]>(() => {
    if (!rows || rows.length === 0) return [];
    const map = new Map<string, { amountAtRisk: number; count: number }>();
    for (const row of rows) {
      const cause = row.root_cause || 'unknown';
      const existing = map.get(cause) || { amountAtRisk: 0, count: 0 };
      existing.amountAtRisk += row.amount_at_risk || 0;
      existing.count += 1;
      map.set(cause, existing);
    }
    return Array.from(map.entries())
      .map(([rootCause, val]) => ({
        rootCause,
        amountAtRisk: val.amountAtRisk,
        count: val.count,
      }))
      .sort((a, b) => b.amountAtRisk - a.amountAtRisk);
  }, [rows]);

  const aiEnabled = isStudioAiEnabled();

  if (isLoading) {
    return <StudioSkeleton />;
  }

  if (isError) {
    return <StudioErrorCard error={error} onRetry={() => refetch()} />;
  }

  if (!rows || rows.length === 0) {
    return <StudioEmptyState currency={currency} onRefresh={() => refetch()} />;
  }

  return (
    <div className="space-y-6 pb-12">
      {/* 1. Page header */}
      <StudioHeader
        currency={currency}
        isFetching={isFetching}
        onRefresh={() => refetch()}
      />

      <div className={`flex flex-col ${aiEnabled ? 'xl:flex-row' : ''} gap-6 items-stretch`}>
        {/* Main Dashboard Layout */}
        <div className="flex-1 min-w-0 space-y-6">
          {/* 2. Row 1: Three equal KPI cards */}
          <StudioKpiCards
            verifiedRecovered={kpiStats.verifiedRecovered}
            amountAtRisk={kpiStats.amountAtRisk}
            eventsCount={kpiStats.eventsCount}
            currency={currency}
          />

          {/* 2 & 3. Row 2: Root-cause chart (~40%) and Grid (~60%) */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
            {/* Chart: left ~40% (5 of 12 cols = 41.7%) */}
            <div className="lg:col-span-5 h-full">
              <StudioRootCauseChart data={rootCauseData} currency={currency} />
            </div>

            {/* Grid: right ~60% (7 of 12 cols = 58.3%) */}
            <div className="lg:col-span-7 h-full">
              <StudioGrid rows={rows} currency={currency} />
            </div>
          </div>

          {/* 5. Footer note */}
          <StudioFooter />
        </div>

        {/* 4. Assistant panel docked on the right when VITE_STUDIO_AI is true */}
        {aiEnabled && (
          <aside className="w-full xl:w-88 shrink-0">
            <StudioAssistantPanel />
          </aside>
        )}
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
