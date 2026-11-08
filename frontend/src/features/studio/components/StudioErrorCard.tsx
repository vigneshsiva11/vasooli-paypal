import React from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, RefreshCw, ArrowLeft } from 'lucide-react';

interface StudioErrorCardProps {
  error: unknown;
  onRetry: () => void;
}

export const StudioErrorCard: React.FC<StudioErrorCardProps> = ({
  error,
  onRetry,
}) => {
  const errorMessage =
    error instanceof Error ? error.message : 'Unable to connect to the backend ledger service.';

  return (
    <div className="mx-auto my-12 flex max-w-lg flex-col items-center rounded-xl border border-red-200 bg-red-50/50 p-8 text-center shadow-xs">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 mb-4">
        <AlertCircle className="h-6 w-6" />
      </div>
      <h2 className="text-xl font-bold text-[var(--color-primary)]">
        Failed to load ledger events
      </h2>
      <p className="mt-2 text-sm text-[var(--color-text-muted)]">
        {errorMessage}
      </p>
      <div className="mt-6 flex items-center gap-4">
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-[var(--color-primary)] hover:bg-gray-50 transition cursor-pointer"
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
};

