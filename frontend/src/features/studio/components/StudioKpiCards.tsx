import React from 'react';
import { formatCurrency } from '@/utils/formatters';

interface StudioKpiCardsProps {
  verifiedRecovered: number;
  amountAtRisk: number;
  eventsCount: number;
  currency: string;
}

export const StudioKpiCards: React.FC<StudioKpiCardsProps> = ({
  verifiedRecovered,
  amountAtRisk,
  eventsCount,
  currency,
}) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      {/* KPI 1: Verified recovered */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col justify-between h-28 transition-all hover:border-gray-300">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Verified recovered
        </span>
        <span className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--color-status-recovered)]">
          {formatCurrency(verifiedRecovered, currency)}
        </span>
      </div>

      {/* KPI 2: Amount at risk */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col justify-between h-28 transition-all hover:border-gray-300">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Amount at risk
        </span>
        <span className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--color-accent)]">
          {formatCurrency(amountAtRisk, currency)}
        </span>
      </div>

      {/* KPI 3: Events */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col justify-between h-28 transition-all hover:border-gray-300">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Events
        </span>
        <span className="text-2xl sm:text-3xl font-bold tracking-tight text-[var(--color-primary)]">
          {eventsCount.toLocaleString()}
        </span>
      </div>
    </div>
  );
};

