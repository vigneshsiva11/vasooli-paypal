import React from 'react';
import { Link } from 'react-router-dom';
import { Layers, ArrowLeft, RefreshCw } from 'lucide-react';

interface StudioEmptyStateProps {
  currency: string;
  onRefresh: () => void;
}

export const StudioEmptyState: React.FC<StudioEmptyStateProps> = ({
  currency,
  onRefresh,
}) => {
  return (
    <div className="mx-auto my-16 flex max-w-md flex-col items-center rounded-2xl border border-gray-200 bg-[var(--color-bg-surface)] p-8 text-center shadow-xs">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white shadow-2xs text-[var(--color-text-muted)] mb-4">
        <Layers className="h-6 w-6 text-gray-400" />
      </div>
      <h2 className="text-lg font-bold text-[var(--color-primary)]">
        No events yet.
      </h2>
      <p className="mt-2 text-sm text-[var(--color-text-muted)]">
        No ledger events found for {currency}. Ingest events or switch currencies to explore the dashboard.
      </p>
      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-[var(--color-primary)] hover:bg-gray-50 transition cursor-pointer"
        >
          <RefreshCw className="h-4 w-4" />
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
};

