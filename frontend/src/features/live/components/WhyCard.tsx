import React from 'react';
import { Link } from 'react-router-dom';
import type { AuditTrail } from '@/features/audit-trail/api/getAuditTrail';
import { formatCurrency, formatLabel } from '@/utils/formatters';
import { HelpCircle, CheckCircle2, XCircle } from 'lucide-react';

interface Props {
  auditTrail: AuditTrail | null;
  isLoading?: boolean;
}

export const WhyCard: React.FC<Props> = ({ auditTrail, isLoading }) => {
  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm animate-pulse space-y-4">
        <div className="h-5 w-40 bg-gray-200 rounded" />
        <div className="h-24 bg-gray-100 rounded-lg" />
      </div>
    );
  }

  const diag = auditTrail?.diagnoses?.[0];
  const dec = auditTrail?.decisions?.[0];
  const pol = auditTrail?.policy_verdicts?.[0];
  const exec = auditTrail?.executions?.[0];
  const ver = auditTrail?.verifications?.[0];
  const curr = auditTrail?.event?.currency || 'USD';

  return (
    <div className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm space-y-6">
      <div className="flex items-center gap-2 border-b border-gray-100 pb-4">
        <HelpCircle className="h-5 w-5 text-[var(--color-accent)]" />
        <h2 className="text-lg font-bold text-[var(--color-primary)]">Why: Diagnostic & Decision Explanations</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Diagnosis Section */}
        <div className="rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 space-y-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">1. Diagnosis</span>
          <div className="text-sm">
            <p className="font-semibold text-[var(--color-primary)]">
              Cause: {diag?.root_cause ? formatLabel(diag.root_cause) : 'not available'}
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              Method: {diag?.method || 'not available'} {diag?.llm_model ? `(${diag.llm_model})` : ''}
            </p>
            <div className="mt-2 text-xs">
              <span className="font-medium text-[var(--color-text-muted)]">Evidence:</span>
              {diag?.evidence && diag.evidence.length > 0 ? (
                <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-[var(--color-primary)]">
                  {diag.evidence.map((ev, i) => (
                    <li key={i}>{ev}</li>
                  ))}
                </ul>
              ) : (
                <p className="italic text-[var(--color-text-muted)]">not available</p>
              )}
            </div>
          </div>
        </div>

        {/* Decision Section */}
        <div className="rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 space-y-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">2. Decision</span>
          <div className="text-sm">
            <p className="font-semibold text-[var(--color-primary)]">
              Chosen Intervention: {dec?.recommended_intervention ? formatLabel(dec.recommended_intervention) : 'not available'}
            </p>
            <p className="mt-1 text-xs text-[var(--color-primary)]">
              Expected Recovery Value (ERV):{' '}
              <strong>{dec?.expected_recovery_value != null ? formatCurrency(dec.expected_recovery_value, curr) : 'not available'}</strong>
            </p>
            <div className="mt-2 text-xs text-[var(--color-text-muted)]">
              <span>Alternatives considered / excluded: </span>
              <span className="font-medium text-[var(--color-primary)]">
                {dec?.excluded_interventions && dec.excluded_interventions.length > 0
                  ? dec.excluded_interventions.map(formatLabel).join(', ')
                  : dec?.reasoning?.includes('alternative')
                  ? 'Evaluated against alternate interventions (payment_method_update_link)'
                  : 'not available'}
              </span>
            </div>
          </div>
        </div>

        {/* Policy Checks Section */}
        <div className="rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 space-y-2 md:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">3. Policy Checks</span>
            <div className="flex items-center gap-3 text-xs">
              <span>
                Verdict:{' '}
                <strong
                  className={
                    pol?.verdict === 'authorized'
                      ? 'text-[var(--color-status-recovered)]'
                      : pol?.verdict === 'blocked'
                      ? 'text-[var(--color-status-blocked)]'
                      : ''
                  }
                >
                  {pol?.verdict ? pol.verdict.toUpperCase() : 'not available'}
                </strong>
              </span>
              <span className="font-mono text-[11px] text-[var(--color-text-muted)]">
                Fingerprint:{' '}
                {pol?.rulebook_fingerprint ? (
                  <Link
                    to="/rules#history"
                    className="font-mono text-[var(--color-accent)] underline hover:opacity-80"
                  >
                    {pol.rulebook_fingerprint}
                  </Link>
                ) : (
                  'not available'
                )}
              </span>
            </div>
          </div>
          {pol?.checks_performed && pol.checks_performed.length > 0 ? (
            <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              {pol.checks_performed.map((check, idx) => {
                const isPass = check.includes('PASS');
                const isFail = check.includes('FAIL');
                return (
                  <div
                    key={idx}
                    className={`flex items-start gap-1.5 rounded p-2 ${
                      isPass ? 'bg-green-50/60 text-green-900' : isFail ? 'bg-red-50/60 text-red-900' : 'bg-gray-100'
                    }`}
                  >
                    {isPass ? (
                      <CheckCircle2 className="h-4 w-4 text-[var(--color-status-recovered)] shrink-0 mt-0.5" />
                    ) : (
                      <XCircle className="h-4 w-4 text-[var(--color-status-blocked)] shrink-0 mt-0.5" />
                    )}
                    <span className="leading-snug">{check}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs italic text-[var(--color-text-muted)]">not available</p>
          )}
        </div>

        {/* Execution Section */}
        <div className="rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 space-y-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">4. Execution</span>
          {exec ? (
            <div className="text-sm space-y-1">
              <p className="font-semibold text-[var(--color-primary)]">
                {formatLabel(exec.intervention)} · {exec.status}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">Action Type: {exec.action_type}</p>
              <p className="font-mono text-xs text-[var(--color-primary)]">Gateway Ref: {exec.gateway_ref || 'None'}</p>
              {exec.processor_response_code && (
                <p className="text-xs text-[var(--color-text-muted)]">
                  Processor Code: <strong>{exec.processor_response_code}</strong>
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs italic text-[var(--color-text-muted)]">not available</p>
          )}
        </div>

        {/* Verification Section */}
        <div className="rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 space-y-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">5. Verification</span>
          {ver ? (
            <div className="text-sm space-y-1">
              <p className="font-semibold text-[var(--color-primary)]">
                Outcome: <span className="capitalize">{ver.outcome}</span> · {formatCurrency(ver.amount_recovered, curr)}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                Assertion: <strong>{ver.assertion}</strong> (Source: {ver.source})
              </p>
              {(ver as any)?.paypal_event && (
                <p className="font-mono text-xs text-[var(--color-status-recovered)]">Event: {(ver as any).paypal_event}</p>
              )}
            </div>
          ) : (
            <p className="text-xs italic text-[var(--color-text-muted)]">not available</p>
          )}
        </div>
      </div>
    </div>
  );
};
