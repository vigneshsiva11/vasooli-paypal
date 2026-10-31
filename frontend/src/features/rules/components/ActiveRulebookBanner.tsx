import React from 'react';
import { ShieldCheck, RotateCcw, Database, Code2 } from 'lucide-react';
import type { ActiveRulebook } from '../api/rulesApi';
import { HARD_CAPS } from '../utils/rulesUtils';

interface Props {
  activeRulebook: ActiveRulebook | null;
  isLoading: boolean;
  isAdminUnlocked: boolean;
  onRevert: () => Promise<void>;
  isReverting: boolean;
  revertMessage?: string | null;
  revertError?: string | null;
}

export const ActiveRulebookBanner: React.FC<Props> = ({
  activeRulebook,
  isLoading,
  isAdminUnlocked,
  onRevert,
  isReverting,
  revertMessage,
  revertError,
}) => {
  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm animate-pulse space-y-3">
        <div className="h-5 w-48 bg-gray-200 rounded" />
        <div className="h-16 bg-gray-100 rounded-lg" />
      </div>
    );
  }

  if (!activeRulebook) {
    return null;
  }

  const isBaseline = activeRulebook.provenance === 'code_baseline';
  const params = activeRulebook.parameters || {};

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-gray-100 pb-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--color-accent)]/10 text-[var(--color-accent)]">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-[var(--color-primary)]">
                Active USD Rulebook
              </h2>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
                  isBaseline
                    ? 'bg-blue-50 text-blue-700 border border-blue-200'
                    : 'bg-purple-50 text-purple-700 border border-purple-200'
                }`}
              >
                {isBaseline ? <Code2 className="h-3 w-3" /> : <Database className="h-3 w-3" />}
                {isBaseline ? 'Code Baseline' : 'Database Override'}
              </span>
            </div>
            <p className="font-mono text-xs text-[var(--color-text-muted)] mt-0.5">
              Fingerprint: <span className="font-semibold text-[var(--color-primary)]">{activeRulebook.fingerprint}</span>
            </p>
          </div>
        </div>

        {isAdminUnlocked && (
          <div className="flex items-center gap-2">
            <button
              onClick={onRevert}
              disabled={isReverting || isBaseline}
              title={isBaseline ? 'Already on code baseline' : 'Revert active rules to baseline'}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-[var(--color-primary)] shadow-sm hover:border-[var(--color-accent)] disabled:opacity-50 transition"
            >
              <RotateCcw className={`h-3.5 w-3.5 ${isReverting ? 'animate-spin' : ''}`} />
              {isReverting ? 'Reverting...' : 'Revert to baseline'}
            </button>
          </div>
        )}
      </div>

      {revertMessage && (
        <div className="rounded-lg bg-green-50 p-3 text-xs font-medium text-green-800 border border-green-200">
          {revertMessage}
        </div>
      )}

      {revertError && (
        <div className="rounded-lg bg-red-50 p-3 text-xs font-medium text-red-800 border border-red-200">
          {revertError}
        </div>
      )}

      {/* Parameters grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 text-xs">
        {Object.entries(HARD_CAPS).map(([key, cap]) => {
          const val = params[key as keyof typeof params];
          return (
            <div key={key} className="rounded-lg border border-gray-100 bg-gray-50/60 p-2.5">
              <span className="block text-[11px] text-[var(--color-text-muted)] leading-tight truncate" title={cap.label}>
                {cap.label}
              </span>
              <span className="mt-1 block font-mono font-semibold text-[var(--color-primary)] text-sm">
                {val != null ? (cap.unit === 'USD' ? `$${val}` : `${val} ${cap.unit}`) : '—'}
              </span>
            </div>
          );
        })}
      </div>

      {activeRulebook.source_text && (
        <div className="rounded-lg bg-gray-50 p-3 text-xs text-[var(--color-text-muted)] border border-gray-100">
          <span className="font-semibold text-[var(--color-primary)]">Source Note: </span>
          <span>{activeRulebook.source_text}</span>
        </div>
      )}
    </div>
  );
};

