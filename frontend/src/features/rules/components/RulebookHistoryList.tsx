import React from 'react';
import { History, CheckCircle2, Clock, UserCheck, Code2, Database } from 'lucide-react';
import type { RulebookHistoryItem } from '../api/rulesApi';
import { HARD_CAPS } from '../utils/rulesUtils';

interface Props {
  history: RulebookHistoryItem[];
  isLoading: boolean;
  activeFingerprint?: string;
}

export const RulebookHistoryList: React.FC<Props> = ({
  history,
  isLoading,
  activeFingerprint,
}) => {
  return (
    <section id="history" className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
      <div className="flex items-center gap-2 border-b border-gray-100 pb-4">
        <History className="h-5 w-5 text-[var(--color-accent)]" />
        <div>
          <h2 className="text-lg font-bold text-[var(--color-primary)]">Rulebook Version History</h2>
          <p className="text-xs text-[var(--color-text-muted)]">
            Chronological audit trail of all deployed USD policy configurations.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3 animate-pulse">
          <div className="h-16 bg-gray-100 rounded-lg" />
          <div className="h-16 bg-gray-100 rounded-lg" />
        </div>
      ) : history.length === 0 ? (
        <div className="rounded-lg border border-gray-100 bg-gray-50 p-6 text-center text-xs text-[var(--color-text-muted)]">
          No rulebook versions recorded in history yet. Code baseline is active.
        </div>
      ) : (
        <div className="space-y-3">
          {history.map((item, idx) => {
            const isActive = item.is_active || item.fingerprint === activeFingerprint;
            const isBaseline = item.provenance === 'code_baseline' || !item.provenance;
            const params = item.parameters || {};

            return (
              <div
                key={`${item.fingerprint}-${idx}`}
                className={`rounded-lg border p-4 transition ${
                  isActive
                    ? 'border-emerald-300 bg-emerald-50/20'
                    : 'border-gray-200 bg-white hover:border-gray-300'
                }`}
              >
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-gray-100 pb-3">
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold ${
                        isActive
                          ? 'bg-emerald-100 text-emerald-800 border border-emerald-200'
                          : 'bg-gray-100 text-gray-700'
                      }`}
                    >
                      {isActive ? (
                        <>
                          <CheckCircle2 className="h-3 w-3 text-emerald-600" /> Active
                        </>
                      ) : (
                        'Archived'
                      )}
                    </span>
                    <span className="font-mono text-xs font-semibold text-[var(--color-primary)]">
                      {item.fingerprint}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        isBaseline ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'
                      }`}
                    >
                      {isBaseline ? <Code2 className="h-2.5 w-2.5" /> : <Database className="h-2.5 w-2.5" />}
                      {isBaseline ? 'Baseline' : 'DB Override'}
                    </span>
                  </div>

                  <div className="flex items-center gap-4 text-xs text-[var(--color-text-muted)]">
                    {item.approved_by && (
                      <span className="inline-flex items-center gap-1">
                        <UserCheck className="h-3.5 w-3.5 text-gray-400" />
                        Approved by: <strong>{item.approved_by}</strong>
                      </span>
                    )}
                    {item.created_at && (
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3.5 w-3.5 text-gray-400" />
                        {new Date(item.created_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                </div>

                {item.source_text && (
                  <p className="mt-2 text-xs italic text-[var(--color-text-muted)]">
                    "{item.source_text}"
                  </p>
                )}

                {/* Parameters pills */}
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  {Object.entries(HARD_CAPS).map(([k, cap]) => {
                    const v = params[k];
                    if (v === undefined || v === null) return null;
                    return (
                      <span
                        key={k}
                        className="rounded bg-gray-100 px-2 py-0.5 text-gray-700 font-medium"
                      >
                        {cap.label}: <strong className="font-mono">{cap.unit === 'USD' ? `$${v}` : `${v} ${cap.unit}`}</strong>
                      </span>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
};

