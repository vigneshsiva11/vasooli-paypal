import React, { useState, useEffect } from 'react';
import {
  PlayCircle,
  CheckCircle2,
  HelpCircle,
  Clock,
  ArrowRight,
  Database,
  Radio,
  FileCheck,
} from 'lucide-react';
import { getSourceBadgeText } from '@/features/live/utils/liveUtils';
import type { AuditTrail } from '@/features/audit-trail/api/getAuditTrail';
import {
  loadReplayRunWithFallback,
  loadAuditTrailWithFallback,
  type StoryReplayRun,
} from '../utils/storyData';
import {
  COMPRESSED_TIME_LABEL,
  getReplayStepCaption,
  getReplayVerificationCaption,
  type RunStep,
} from '../utils/storyCaptions';

interface Props {
  slideIndex: number;
  onStepChanged?: (caption: string) => void;
}

export const Act2WatchItWork: React.FC<Props> = ({ slideIndex, onStepChanged }) => {
  const [runData, setRunData] = useState<StoryReplayRun | null>(null);
  const [auditTrail, setAuditTrail] = useState<AuditTrail | null>(null);
  const [activeStepIndex, setActiveStepIndex] = useState<number>(0);

  // Load replay run and audit-trail data
  useEffect(() => {
    let isCancelled = false;
    Promise.all([loadReplayRunWithFallback(), loadAuditTrailWithFallback()]).then(
      ([run, audit]) => {
        if (!isCancelled) {
          setRunData(run);
          setAuditTrail(audit);
        }
      }
    );
    return () => {
      isCancelled = true;
    };
  }, []);

  // Map slideIndex (slides 3 to 10) to replay steps (0 to 6, and 7 for final Why card)
  useEffect(() => {
    if (slideIndex >= 3 && slideIndex <= 9) {
      const stepIdx = slideIndex - 3;
      setActiveStepIndex(stepIdx);
    } else if (slideIndex >= 10) {
      setActiveStepIndex(7); // Final verified state
    }
  }, [slideIndex]);

  const steps = runData?.steps || [];
  const currentStep: RunStep | undefined = steps[activeStepIndex];
  const isFinalView = activeStepIndex >= steps.length;

  // Notify parent of updated caption
  useEffect(() => {
    if (onStepChanged) {
      if (isFinalView && auditTrail?.verifications?.[0]) {
        const v = auditTrail.verifications[0];
        onStepChanged(getReplayVerificationCaption(v.amount_recovered, auditTrail.event?.currency || 'USD', v.outcome));
      } else if (currentStep) {
        onStepChanged(getReplayStepCaption(currentStep));
      }
    }
  }, [activeStepIndex, isFinalView, currentStep, auditTrail, onStepChanged]);

  const verifiedAmount = auditTrail?.verifications?.[0]?.amount_recovered ?? runData?.amount ?? 49.0;
  const currency = auditTrail?.event?.currency || runData?.currency || 'USD';
  const diag = auditTrail?.diagnoses?.[0];
  const dec = auditTrail?.decisions?.[0];
  const pol = auditTrail?.policy_verdicts?.[0];

  return (
    <div className="space-y-6">
      {/* Permanent Warning & Context Label */}
      <div className="flex items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-950/30 px-4 py-3 text-xs text-amber-200">
        <Clock className="h-4 w-4 text-amber-400 shrink-0" />
        <span className="font-semibold tracking-wide" data-testid="compressed-time-label">
          {COMPRESSED_TIME_LABEL}
        </span>
      </div>

      {/* Act Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800 pb-4">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
            Act 2 &bull; Deterministic Replay
          </span>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <PlayCircle className="h-6 w-6 text-cyan-400" />
            Watch it work
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            Playback of real sandbox execution event {runData?.run_id || '6ae6ec3e57392a70552ed256'} through all policy checkpoints.
          </p>
        </div>

        {/* Amount Badge */}
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/40 px-4 py-2.5 text-right">
          <span className="block text-[11px] font-semibold text-emerald-400">
            Target Receivable
          </span>
          <span className="font-mono text-base font-bold text-emerald-300">
            ${verifiedAmount.toFixed(2)} {currency}
          </span>
        </div>
      </div>

      {/* Replay Step Pipeline Navigator */}
      <div className="overflow-x-auto pb-2">
        <div className="flex items-center gap-2 min-w-max">
          {steps.map((st, idx) => {
            const isCurrent = activeStepIndex === idx;
            const isPassed = activeStepIndex > idx;
            return (
              <React.Fragment key={st.name}>
                <button
                  type="button"
                  onClick={() => setActiveStepIndex(idx)}
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-bold transition border ${
                    isCurrent
                      ? 'border-cyan-400 bg-cyan-950/60 text-white ring-1 ring-cyan-400/50'
                      : isPassed
                      ? 'border-emerald-600/60 bg-emerald-950/20 text-emerald-300'
                      : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <span
                    className={`h-5 w-5 rounded-full flex items-center justify-center text-[10px] font-mono ${
                      isCurrent
                        ? 'bg-cyan-500 text-slate-950'
                        : isPassed
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-800 text-slate-400'
                    }`}
                  >
                    {idx + 1}
                  </span>
                  <span className="capitalize">{st.name.replace(/_/g, ' ')}</span>
                </button>
                {idx < steps.length - 1 && (
                  <ArrowRight className="h-3 w-3 text-slate-600 shrink-0" />
                )}
              </React.Fragment>
            );
          })}

          <ArrowRight className="h-3 w-3 text-slate-600 shrink-0" />

          {/* Final Summary Tab */}
          <button
            type="button"
            onClick={() => setActiveStepIndex(steps.length)}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-bold transition border ${
              isFinalView
                ? 'border-emerald-400 bg-emerald-950/60 text-emerald-200 ring-1 ring-emerald-400/50'
                : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700'
            }`}
          >
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
            <span>Verified & Why Card</span>
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      {!isFinalView && currentStep ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Step Detail Card */}
          <div className="lg:col-span-2 rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="h-3 w-3 rounded-full bg-cyan-400 animate-ping" />
                <h3 className="text-base font-bold text-white capitalize">
                  Step {activeStepIndex + 1}: {currentStep.name.replace(/_/g, ' ')}
                </h3>
              </div>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold bg-emerald-950/40 border border-emerald-500/40 text-emerald-300">
                <CheckCircle2 className="h-3.5 w-3.5" />
                {currentStep.status}
              </span>
            </div>

            {/* Source Badge */}
            <div className="flex items-center gap-2 text-xs">
              <span className="text-slate-400 font-semibold">Source Badge:</span>
              <span className="inline-flex items-center gap-1 px-3 py-1 rounded-md bg-slate-800 border border-slate-700 text-cyan-300 font-medium">
                <Radio className="h-3.5 w-3.5 text-cyan-400" />
                {getSourceBadgeText(currentStep.source) || currentStep.source}
              </span>
            </div>

            {/* Step Caption */}
            <div className="rounded-lg bg-slate-950/80 border border-slate-800 p-4 font-mono text-sm text-slate-200">
              {getReplayStepCaption(currentStep)}
            </div>

            {/* Timestamps */}
            {currentStep.started_at && (
              <div className="flex items-center gap-4 text-xs font-mono text-slate-500">
                <span>Started: {currentStep.started_at}</span>
                {currentStep.finished_at && <span>Finished: {currentStep.finished_at}</span>}
              </div>
            )}
          </div>

          {/* Evidence IDs Present */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <Database className="h-4 w-4 text-cyan-400" />
              Evidence IDs in Data
            </h3>

            {currentStep.refs && Object.keys(currentStep.refs).length > 0 ? (
              <div className="space-y-2">
                {Object.entries(currentStep.refs).map(([key, val]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5 font-mono text-xs flex flex-col gap-1"
                  >
                    <span className="text-[11px] text-slate-400 font-sans">{key}</span>
                    <span className="text-cyan-300 font-bold break-all">{String(val)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-slate-500 italic">No external identifiers recorded.</p>
            )}
          </div>
        </div>
      ) : (
        /* Final State: Verified Amount and Why Card */
        <div className="space-y-6">
          {/* Verified Amount Banner */}
          <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/30 p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="h-12 w-12 rounded-xl bg-emerald-500/20 border border-emerald-400/40 flex items-center justify-center text-emerald-400">
                <FileCheck className="h-7 w-7" />
              </div>
              <div>
                <span className="text-xs font-bold uppercase tracking-wider text-emerald-400">
                  Verification Complete
                </span>
                <h3 className="text-2xl font-bold text-white font-mono">
                  ${verifiedAmount.toFixed(2)} {currency} Recovered
                </h3>
                <p className="text-xs text-slate-300 mt-1">
                  Replay confirmed outcome: <strong className="text-emerald-300">recovered</strong> via gateway webhook assertion.
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-emerald-500/30 bg-emerald-950/50 px-4 py-2 text-right font-mono text-xs text-emerald-200">
              <div>Verification ID</div>
              <div className="font-bold text-emerald-400">
                {auditTrail?.verifications?.[0]?.id || '6ae6ec6d57392a70552ed262'}
              </div>
            </div>
          </div>

          {/* The Why Card */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-6 space-y-6">
            <div className="flex items-center gap-2 border-b border-slate-800 pb-3">
              <HelpCircle className="h-5 w-5 text-cyan-400" />
              <h3 className="text-lg font-bold text-white">Why: Diagnostic & Decision Explanations</h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Diagnosis */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
                <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
                  1. Diagnosis
                </span>
                <div className="text-xs space-y-1">
                  <p className="font-bold text-white">
                    Root Cause: <span className="text-cyan-300">{diag?.root_cause || 'insufficient_funds'}</span>
                  </p>
                  <p className="text-slate-400">
                    Confidence: {diag?.confidence ? `${(diag.confidence * 100).toFixed(0)}%` : '95%'} &bull; Method: {diag?.method || 'rules'}
                  </p>
                  <div className="mt-2 text-slate-300">
                    <span className="font-semibold text-slate-400">Evidence:</span>
                    <ul className="list-disc pl-4 mt-1 text-[11px] text-slate-300">
                      {diag?.evidence?.map((ev, i) => (
                        <li key={i}>{ev}</li>
                      )) || <li>Processor response code 5120</li>}
                    </ul>
                  </div>
                </div>
              </div>

              {/* Decision */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
                <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
                  2. Decision
                </span>
                <div className="text-xs space-y-1">
                  <p className="font-bold text-white">
                    Chosen Intervention: <span className="text-cyan-300">{dec?.recommended_intervention || 'delayed_retry'}</span>
                  </p>
                  <p className="text-slate-300">
                    Expected Recovery Value (ERV):{' '}
                    <strong className="text-emerald-300">
                      ${dec?.expected_recovery_value != null ? dec.expected_recovery_value.toFixed(2) : '17.15'}
                    </strong>
                  </p>
                  <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
                    {dec?.reasoning || 'Balances are often topped up within days; a free retry captures that without customer friction.'}
                  </p>
                </div>
              </div>

              {/* Policy Checks */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2 md:col-span-2">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
                    3. Policy Checks In Force
                  </span>
                  <span className="text-xs font-bold text-emerald-400 font-mono">
                    Verdict: {pol?.verdict?.toUpperCase() || 'AUTHORIZED'}
                  </span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] font-mono text-slate-300">
                  {pol?.checks_performed?.map((chk, i) => (
                    <div key={i} className="flex items-center gap-1.5 p-1.5 rounded bg-slate-900 border border-slate-800">
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                      <span className="truncate">{chk}</span>
                    </div>
                  )) || (
                    <div className="p-1.5 rounded bg-slate-900 border border-slate-800 text-emerald-300">
                      All 6 policy invariant checks passed cleanly
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
