import React from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
  error: string;
}

export const ConfigErrorScreen: React.FC<Props> = ({ error }) => {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-[var(--color-bg-base)] p-6 text-[var(--color-primary)]">
      <div className="max-w-lg w-full rounded-2xl border border-[var(--color-status-blocked)]/30 bg-white p-8 shadow-lg text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-[var(--color-status-blocked)]/10 text-[var(--color-status-blocked)]">
          <AlertTriangle className="h-8 w-8" />
        </div>
        <h1 className="mt-4 text-2xl font-bold tracking-tight text-[var(--color-primary)]">
          Configuration Error
        </h1>
        <p className="mt-2 text-sm text-[var(--color-text-muted)] leading-relaxed">
          The application cannot start because the API base URL is missing or invalid.
        </p>
        <div className="mt-5 rounded-lg border border-red-100 bg-red-50/50 p-4 text-left font-mono text-xs text-[var(--color-status-blocked)] break-all">
          <strong>VITE_API_BASE_URL:</strong> {error}
        </div>
        <p className="mt-4 text-xs text-[var(--color-text-muted)]">
          Please provide a valid absolute HTTP or HTTPS URL for <code>VITE_API_BASE_URL</code> in your environment.
        </p>
      </div>
    </div>
  );
};

