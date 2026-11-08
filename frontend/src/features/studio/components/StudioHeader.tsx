import React from 'react';
import { RefreshCw, CheckCircle2 } from 'lucide-react';

interface StudioHeaderProps {
  currency: string;
  isFetching: boolean;
  onRefresh: () => void;
}

export const StudioHeader: React.FC<StudioHeaderProps> = ({
  currency,
  isFetching,
  onRefresh,
}) => {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-3xl font-bold tracking-tight text-[var(--color-primary)]">
            Studio
          </h1>
          <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800">
            Sandbox data
          </span>
          <span className="inline-flex items-center rounded-md border border-gray-200 bg-white px-2.5 py-0.5 text-xs font-semibold text-[var(--color-primary)] shadow-2xs">
            Currency: {currency}
          </span>
        </div>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Interactive recovery analytics and verified vs asserted ledger workbench.
        </p>
        <div className="mt-2 flex items-center gap-1.5 text-xs font-medium text-[var(--color-status-recovered)]">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
          <span>Verified = confirmed by a PayPal webhook</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onRefresh}
          disabled={isFetching}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-[var(--color-primary)] shadow-2xs hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] disabled:opacity-50 transition cursor-pointer"
          aria-label="Refresh ledger events"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>
    </div>
  );
};

