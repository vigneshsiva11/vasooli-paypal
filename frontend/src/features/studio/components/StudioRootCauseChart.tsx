import React from 'react';
import { formatCurrency } from '@/utils/formatters';
import { humanizeRootCause } from '../utils/studioFormatters';

export interface RootCauseItem {
  rootCause: string;
  amountAtRisk: number;
  count: number;
}

interface StudioRootCauseChartProps {
  data: RootCauseItem[];
  currency: string;
}

export const StudioRootCauseChart: React.FC<StudioRootCauseChartProps> = ({
  data,
  currency,
}) => {
  const maxRisk = Math.max(...data.map((d) => d.amountAtRisk), 1);

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col h-full min-h-[460px] justify-between">
      <div>
        <div className="flex items-center justify-between pb-3 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-[var(--color-primary)]">
              Amount at Risk by Root Cause
            </h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              Aggregated exposure by failure trigger
            </p>
          </div>
          <span className="text-xs font-medium text-[var(--color-text-muted)]">
            {data.length} categories
          </span>
        </div>

        {data.length === 0 ? (
          <div className="flex h-64 items-center justify-center text-sm text-[var(--color-text-muted)]">
            No root cause data available
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            {data.map((item) => {
              const percentage = Math.round((item.amountAtRisk / maxRisk) * 100);
              const humanLabel = humanizeRootCause(item.rootCause);
              const formattedVal = formatCurrency(item.amountAtRisk, currency);

              return (
                <div key={item.rootCause} className="group">
                  {/* Axis Label - humanized & readable */}
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span
                      data-testid={`root-cause-label-${item.rootCause}`}
                      className="font-medium text-[var(--color-primary)] truncate max-w-[70%]"
                      title={humanLabel}
                    >
                      {humanLabel}
                    </span>
                    <span className="text-[var(--color-text-muted)] font-mono text-[11px]">
                      {item.count} {item.count === 1 ? 'event' : 'events'}
                    </span>
                  </div>

                  {/* Horizontal Bar with value label on the bar */}
                  <div className="relative h-7 w-full rounded-md bg-[var(--color-bg-surface)] overflow-hidden flex items-center p-0.5">
                    <div
                      role="progressbar"
                      aria-valuenow={item.amountAtRisk}
                      aria-valuemin={0}
                      aria-valuemax={maxRisk}
                      aria-label={`${humanLabel}: ${formattedVal}`}
                      className="h-full rounded bg-[var(--color-accent)] transition-all duration-500 ease-out flex items-center justify-end px-2"
                      style={{
                        width: `${Math.max(percentage, 12)}%`,
                      }}
                    >
                      {/* Value label on the bar when wide enough */}
                      {percentage >= 35 && (
                        <span
                          data-testid={`bar-value-${item.rootCause}`}
                          className="text-[11px] font-bold text-white tracking-tight drop-shadow-2xs select-none"
                        >
                          {formattedVal}
                        </span>
                      )}
                    </div>

                    {/* Value label right outside bar when bar is short */}
                    {percentage < 35 && (
                      <span
                        data-testid={`bar-value-${item.rootCause}`}
                        className="ml-2 text-[11px] font-bold text-[var(--color-primary)] font-mono select-none"
                      >
                        {formattedVal}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Axis legend / baseline reference */}
      <div className="pt-3 border-t border-gray-100 flex items-center justify-between text-[11px] text-[var(--color-text-muted)] font-mono">
        <span>0</span>
        <span>{formatCurrency(Math.round(maxRisk / 2), currency)}</span>
        <span>{formatCurrency(maxRisk, currency)}</span>
      </div>
    </div>
  );
};

