import React from 'react';
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ArrowRight,
  Send,
} from 'lucide-react';
import type { RulePreviewResponse } from '../api/rulesApi';
import { HARD_CAPS, evaluateHardCaps } from '../utils/rulesUtils';

interface Props {
  preview: RulePreviewResponse;
  activeFingerprint?: string;
  isAdminUnlocked: boolean;
  onApply: (previewId: string, expectedParentFingerprint: string) => Promise<void>;
  isApplying: boolean;
  applyMessage?: string | null;
  applyError?: string | null;
}

export const RulePreviewResults: React.FC<Props> = ({
  preview,
  activeFingerprint,
  isAdminUnlocked,
  onApply,
  isApplying,
  applyMessage,
  applyError,
}) => {
  const capEvaluation = evaluateHardCaps(preview.proposed_parameters, preview.errors);
  const diffEntries = Object.entries(preview.diff || {});
  const parentFp = preview.current_fingerprint || activeFingerprint || '';

  // Gather all unique parameters from proposed_parameters, parameter_sources, or diff
  const paramKeys = Array.from(
    new Set([
      ...Object.keys(preview.proposed_parameters || {}),
      ...Object.keys(preview.parameter_sources || {}),
      ...Object.keys(preview.diff || {}),
    ])
  );

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-6">
      {/* Header and Prospective Fingerprint */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-gray-100 pb-4">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">
            Rulebook Proposal Preview
          </span>
          <div className="mt-1 flex items-center gap-2">
            <h2 className="text-lg font-bold text-[var(--color-primary)]">Evaluation Results</h2>
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-bold ${
                preview.valid
                  ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                  : 'bg-red-50 text-red-700 border border-red-200'
              }`}
            >
              {preview.valid ? (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Valid Proposal
                </>
              ) : (
                <>
                  <XCircle className="h-3.5 w-3.5" /> Proposal Rejected
                </>
              )}
            </span>
          </div>
        </div>

        {/* Prospective Fingerprint */}
        <div className="rounded-lg border border-gray-100 bg-gray-50/70 px-3 py-2 text-right">
          <span className="block text-[11px] font-semibold text-[var(--color-text-muted)]">
            Prospective Fingerprint
          </span>
          <span className="font-mono text-xs font-bold text-[var(--color-primary)]">
            {preview.prospective_fingerprint || 'None (proposal rejected)'}
          </span>
        </div>
      </div>

      {/* Hard-Cap Results */}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
          Hard-Cap & Invariant Check
        </h3>
        {capEvaluation.passed && preview.valid ? (
          <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50/70 p-3.5 text-xs text-emerald-900">
            <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold">PASS: Hard safety caps and system invariants satisfied.</p>
              <p className="mt-0.5 text-emerald-700">
                All proposed parameter values fall strictly within permissible merchant limits and respect core invariants.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50/70 p-3.5 text-xs text-red-900">
              <XCircle className="h-4 w-4 text-red-600 shrink-0 mt-0.5" />
              <div>
                <p className="font-bold">REJECT: Proposal violates hard caps or invariant constraints.</p>
                <ul className="mt-1.5 list-disc space-y-1 pl-4 text-red-800">
                  {capEvaluation.reasons.map((reason, idx) => (
                    <li key={idx} className="leading-snug">
                      {reason}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Parameter Table: parameter, current, proposed, verbatim source phrase */}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
          Parameter Grounding & Values
        </h3>
        {paramKeys.length > 0 ? (
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-left text-xs">
              <thead className="bg-gray-50/80 text-[var(--color-text-muted)] border-b border-gray-200">
                <tr>
                  <th className="py-2.5 px-3 font-semibold">Parameter</th>
                  <th className="py-2.5 px-3 font-semibold">Current</th>
                  <th className="py-2.5 px-3 font-semibold">Proposed</th>
                  <th className="py-2.5 px-3 font-semibold">Verbatim Source Phrase</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {paramKeys.map((key) => {
                  const cap = HARD_CAPS[key];
                  const label = cap?.label || key;
                  const currentVal = preview.diff?.[key]?.current != null ? preview.diff[key].current : '—';
                  const proposedVal = preview.proposed_parameters?.[key] ?? preview.diff?.[key]?.proposed ?? '—';
                  const sourcePhrase = preview.parameter_sources?.[key] || '—';

                  return (
                    <tr key={key} className="hover:bg-gray-50/50">
                      <td className="py-2.5 px-3 font-medium text-[var(--color-primary)]">
                        <div>{label}</div>
                        <span className="font-mono text-[10px] text-gray-400">{key}</span>
                      </td>
                      <td className="py-2.5 px-3 font-mono text-[var(--color-text-muted)]">
                        {String(currentVal)}
                      </td>
                      <td className="py-2.5 px-3 font-mono font-bold text-[var(--color-primary)]">
                        {String(proposedVal)}
                      </td>
                      <td className="py-2.5 px-3 italic text-[var(--color-primary)]">
                        {sourcePhrase !== '—' ? `"${sourcePhrase}"` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-4 text-center text-xs text-[var(--color-text-muted)]">
            No supported USD parameters proposed in input text.
          </div>
        )}
      </div>

      {/* The Diff */}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
          Rulebook Diff
        </h3>
        {diffEntries.length > 0 ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50/60 p-3 space-y-2">
            {diffEntries.map(([param, change]) => {
              const cap = HARD_CAPS[param];
              const label = cap?.label || param;
              return (
                <div
                  key={param}
                  className="flex flex-wrap items-center justify-between rounded bg-white px-3 py-2 border border-gray-100 shadow-2xs text-xs"
                >
                  <span className="font-semibold text-[var(--color-primary)]">{label}</span>
                  <div className="flex items-center gap-2 font-mono">
                    <span className="text-gray-500">{String(change.current)}</span>
                    <ArrowRight className="h-3 w-3 text-gray-400" />
                    <span className="font-bold text-[var(--color-accent)]">{String(change.proposed)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs text-[var(--color-text-muted)]">
            No parameter changes from current rulebook baseline.
          </div>
        )}
      </div>

      {/* The Not Applied list */}
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
          Not Applied / Ignored Rules
        </h3>
        {preview.not_applied && preview.not_applied.length > 0 ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50/40 divide-y divide-amber-100">
            {preview.not_applied.map((item, idx) => (
              <div key={idx} className="p-3 text-xs">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600 shrink-0 mt-0.5" />
                  <div>
                    <span className="font-bold text-amber-950">Requested Rule: </span>
                    <span className="font-mono text-amber-900">{item.requested_rule}</span>
                  </div>
                </div>
                <div className="mt-1 pl-5.5 text-amber-800">
                  <span className="font-semibold">Reason: </span>
                  <span>{item.reason}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs text-[var(--color-text-muted)]">
            None — no conflicting or unsupported instructions were skipped.
          </div>
        )}
      </div>

      {/* Admin Action: Apply Proposal */}
      <div className="border-t border-gray-100 pt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="text-xs text-[var(--color-text-muted)]">
          {isAdminUnlocked ? (
            <span>
              Target Parent Fingerprint: <span className="font-mono font-semibold">{parentFp}</span>
            </span>
          ) : (
            <span className="font-medium text-amber-700">
              Preview only mode. Unlock operator permissions above to apply changes.
            </span>
          )}
        </div>

        {isAdminUnlocked && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => onApply(preview.preview_id, parentFp)}
              disabled={isApplying || !preview.valid}
              title={!preview.valid ? 'Cannot apply rejected proposal' : 'Apply proposal to active rulebook'}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-xs font-bold text-white shadow-sm hover:opacity-95 disabled:opacity-50 transition"
            >
              {isApplying ? (
                <>Applying Rulebook...</>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5" /> Apply Proposal
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {applyMessage && (
        <div className="rounded-lg bg-green-50 p-3 text-xs font-medium text-green-800 border border-green-200">
          {applyMessage}
        </div>
      )}

      {applyError && (
        <div className="rounded-lg bg-red-50 p-3 text-xs font-medium text-red-800 border border-red-200">
          {applyError}
        </div>
      )}
    </div>
  );
};
