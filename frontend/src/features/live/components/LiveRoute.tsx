import React, { useState, useEffect, useMemo, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Play,
  RotateCcw,
  Zap,
  ShieldCheck,
  AlertTriangle,
  Radio,
  FileCheck2,
  Pause,
} from 'lucide-react';
import { useLiveRunsQuery, useTriggerPublicRunMutation, getReplayData, type DemoRun, type DemoStep } from '../api/getLiveRuns';
import { useLedgerQuery } from '@/features/ledger/api/getLedger';
import { useAuditTrailQuery } from '@/features/audit-trail/api/getAuditTrail';
import {
  getSourceBadgeText,
  handleRunTriggerError,
  extractEvidenceFromStepRefs,
  extractDeclineEventId,
} from '../utils/liveUtils';
import { WhyCard } from './WhyCard';
import { formatCurrency, formatLabel } from '@/utils/formatters';

export const LiveRoute: React.FC = () => {
  const [mode, setMode] = useState<'live' | 'replay'>('live');

  // Replay State
  const [replayRun, setReplayRun] = useState<DemoRun | null>(null);
  const [replayAudit, setReplayAudit] = useState<any>(null);
  const [replayStepIndex, setReplayStepIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const replayTimerRef = useRef<any>(null);

  // Trigger error state & 429 countdown
  const [triggerError, setTriggerError] = useState<{ disabled: boolean; message: string } | null>(null);
  const [retryCountdown, setRetryCountdown] = useState<number | null>(null);

  // Load Replay Data
  useEffect(() => {
    getReplayData()
      .then(({ runData, auditData }) => {
        const recoveredRun =
          runData.runs.find((r) => r.status === 'recovered' || r.final_status === 'recovered') || runData.runs[0];
        setReplayRun(recoveredRun || null);
        setReplayAudit(auditData);
        if (recoveredRun?.steps?.length) {
          setReplayStepIndex(recoveredRun.steps.length);
        }
      })
      .catch((err) => {
        console.warn('Could not load replay fixtures:', err);
      });
  }, []);

  // Replay playback timer
  useEffect(() => {
    if (isPlaying && replayRun) {
      replayTimerRef.current = setInterval(() => {
        setReplayStepIndex((prev) => {
          if (prev >= (replayRun.steps?.length ?? 0)) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1500);
    } else {
      if (replayTimerRef.current) clearInterval(replayTimerRef.current);
    }
    return () => {
      if (replayTimerRef.current) clearInterval(replayTimerRef.current);
    };
  }, [isPlaying, replayRun]);

  // 429 countdown timer
  useEffect(() => {
    if (retryCountdown === null || retryCountdown <= 0) return;
    const interval = setInterval(() => {
      setRetryCountdown((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(interval);
          setTriggerError(null);
          return null;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [retryCountdown]);

  // Live Query
  const { data: runsData, isError: liveRunsError } = useLiveRunsQuery(mode === 'live');
  const latestLiveRun = runsData?.runs?.[0] || null;

  // Active run check for 3s polling
  const isRunActive =
    latestLiveRun?.status === 'running' ||
    latestLiveRun?.status === 'in_progress' ||
    latestLiveRun?.final_status === 'running';

  // Selected Run based on mode
  const currentRun = mode === 'replay' ? replayRun : latestLiveRun;

  // Displayed Steps
  const displayedSteps: DemoStep[] = useMemo(() => {
    if (!currentRun?.steps) return [];
    if (mode === 'replay') {
      return currentRun.steps.slice(0, replayStepIndex);
    }
    return currentRun.steps;
  }, [currentRun, mode, replayStepIndex]);

  // Target event ID from decline_ingested step refs
  const eventId = useMemo(() => {
    return extractDeclineEventId(currentRun?.steps || []);
  }, [currentRun]);

  // Ledger query to verify assertion and recovered amount
  const { data: ledgerRows = [] } = useLedgerQuery(currentRun?.currency);
  const ledgerRow = useMemo(() => {
    if (!eventId) return null;
    return ledgerRows.find((r) => r.event_id === eventId) || null;
  }, [eventId, ledgerRows]);

  // Live Audit Trail query for the Why card
  const { data: liveAuditTrail, isLoading: auditLoading } = useAuditTrailQuery(mode === 'live' ? eventId : null);
  const effectiveAuditTrail = mode === 'replay' ? replayAudit : liveAuditTrail;

  // Evidence list built strictly from IDs present in step refs
  const evidenceList = useMemo(() => {
    return extractEvidenceFromStepRefs(displayedSteps);
  }, [displayedSteps]);

  // Run it live mutation
  const triggerRunMutation = useTriggerPublicRunMutation();

  const handleRunLive = () => {
    if (retryCountdown !== null && retryCountdown > 0) return;
    setTriggerError(null);
    triggerRunMutation.mutate(undefined, {
      onError: (err: any) => {
        const res = handleRunTriggerError(err);
        setTriggerError({ disabled: res.disabled, message: res.message });
        if (res.retryAfterSeconds) {
          setRetryCountdown(res.retryAfterSeconds);
        }
      },
    });
  };

  // Hero card details
  const verifiedAmount = ledgerRow
    ? ledgerRow.verified_amount
    : currentRun?.status === 'recovered' || currentRun?.final_status === 'recovered'
    ? currentRun.amount
    : 0;
  const isGatewayVerified = ledgerRow?.assertion === 'gateway_verified' || (mode === 'replay' && replayAudit?.verifications?.[0]?.assertion === 'gateway_verified');
  const heroBadgeLabel = isGatewayVerified ? 'Verified by PayPal webhook' : 'Awaiting verification';

  return (
    <div className="space-y-8 pb-16">
      {/* (g) Top Banner */}
      <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50/80 px-5 py-3 text-sm text-amber-900 shadow-sm">
        <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
        <span className="font-semibold">Sandbox. No real money. Webhooks take about a minute.</span>
      </div>

      {/* Header and Controls */}
      <header className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Radio className="h-5 w-5 text-[var(--color-accent)] animate-pulse" />
            <h1 className="text-3xl font-bold tracking-tight text-[var(--color-primary)]">Mission Control</h1>
          </div>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Live pipeline execution, PayPal webhook verification, and automated revenue recovery.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Mode Switcher */}
          <div className="flex rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
            <button
              onClick={() => {
                setMode('live');
                setIsPlaying(false);
              }}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                mode === 'live'
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
              }`}
            >
              Live Monitor
            </button>
            <button
              onClick={() => {
                setMode('replay');
                setReplayStepIndex(replayRun?.steps?.length || 7);
              }}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                mode === 'replay'
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-primary)]'
              }`}
            >
              Recorded Replay
            </button>
          </div>

          {/* Mode-specific actions */}
          {mode === 'replay' ? (
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  if (replayStepIndex >= (replayRun?.steps?.length ?? 0)) {
                    setReplayStepIndex(0);
                  }
                  setIsPlaying(!isPlaying);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-[var(--color-primary)] shadow-sm hover:border-[var(--color-accent)]"
              >
                {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                {isPlaying ? 'Pause' : replayStepIndex >= (replayRun?.steps?.length ?? 0) ? 'Replay' : 'Play'}
              </button>
              <button
                onClick={() => {
                  setIsPlaying(false);
                  setReplayStepIndex(0);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-[var(--color-primary)] shadow-sm hover:border-[var(--color-accent)]"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Restart
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-end">
              <button
                onClick={handleRunLive}
                disabled={triggerError?.disabled || triggerRunMutation.isPending || isRunActive || (retryCountdown !== null && retryCountdown > 0)}
                className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-4 py-2.5 text-xs font-bold text-white shadow-sm hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 transition"
              >
                <Zap className="h-3.5 w-3.5" />
                {triggerRunMutation.isPending
                  ? 'Initiating run…'
                  : retryCountdown !== null && retryCountdown > 0
                  ? `Wait ${retryCountdown}s`
                  : isRunActive
                  ? 'Run in progress…'
                  : 'Run it live'}
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Trigger Error Message */}
      {triggerError && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs font-medium text-amber-900">
          <p>{triggerError.message}</p>
        </div>
      )}

      {/* Hero Card */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="rounded-2xl border border-[var(--color-status-recovered)]/30 bg-gradient-to-br from-white to-green-50/30 p-6 shadow-sm md:col-span-2 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                Verified Recovered Amount
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-bold ${
                  isGatewayVerified
                    ? 'bg-[var(--color-status-recovered)] text-white'
                    : 'bg-amber-100 text-amber-800'
                }`}
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                {heroBadgeLabel}
              </span>
            </div>
            <div className="mt-4 flex items-baseline gap-3">
              <span className="text-4xl font-extrabold text-[var(--color-primary)]">
                {formatCurrency(verifiedAmount, currentRun?.currency || 'USD')}
              </span>
              <span className="text-sm font-semibold text-[var(--color-text-muted)]">
                of {formatCurrency(currentRun?.amount || 49.0, currentRun?.currency || 'USD')} at risk
              </span>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-4 text-xs text-[var(--color-text-muted)] border-t border-gray-100 pt-4">
            <span>
              Customer Ref: <strong className="font-mono text-[var(--color-primary)]">{currentRun?.customer_ref || 'demo-001'}</strong>
            </span>
            <span>
              Scenario: <strong className="text-[var(--color-primary)]">{currentRun?.scenario || 'insufficient-funds'}</strong>
            </span>
            {eventId && (
              <span>
                Event ID: <strong className="font-mono text-[var(--color-primary)]">{eventId}</strong>
              </span>
            )}
          </div>
        </div>

        {/* Run Status Widget */}
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Run Status</span>
            <p className="mt-2 text-2xl font-bold capitalize text-[var(--color-primary)]">
              {currentRun?.final_status || currentRun?.status || (mode === 'live' && liveRunsError ? 'Backend Offline' : 'Ready')}
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              {mode === 'replay'
                ? `Step ${displayedSteps.length} of ${currentRun?.steps?.length ?? 0}`
                : isRunActive
                ? 'Active pipeline execution in progress (polling every 3s)'
                : 'Showing most recent execution run'}
            </p>
          </div>
          <div className="mt-4 pt-4 border-t border-gray-100 text-xs font-mono text-[var(--color-text-muted)] truncate">
            Run ID: {currentRun?.run_id || 'None'}
          </div>
        </div>
      </div>

      {/* (a) Real Pipeline Steps */}
      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-100 pb-4">
          <div>
            <h2 className="text-lg font-bold text-[var(--color-primary)]">Pipeline Execution Steps</h2>
            <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
              Ordered audit chain of actions with attestation sources.
            </p>
          </div>
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-[var(--color-text-muted)]">
            {displayedSteps.length} Steps
          </span>
        </div>

        <div className="mt-6 space-y-4">
          <AnimatePresence>
            {displayedSteps.map((step, idx) => {
              const badgeText = getSourceBadgeText(step.source);
              const isCompleted = step.status === 'completed';
              return (
                <motion.div
                  key={`${step.name}-${idx}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-gray-100 bg-[var(--color-bg-surface)] p-4 transition-all"
                >
                  <div className="flex items-start gap-3">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-xs font-bold text-[var(--color-primary)] border border-gray-200 shadow-xs">
                      {idx + 1}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-bold text-[var(--color-primary)]">{formatLabel(step.name)}</p>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                            isCompleted
                              ? 'bg-[var(--color-status-recovered)]/15 text-[var(--color-status-recovered)]'
                              : 'bg-amber-100 text-amber-800'
                          }`}
                        >
                          {step.status}
                        </span>
                      </div>
                      {step.finished_at && (
                        <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
                          Completed: {new Date(step.finished_at).toLocaleTimeString()}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 pl-9 sm:pl-0">
                    <span className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-[var(--color-primary)] shadow-2xs">
                      {badgeText}
                    </span>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>

          {displayedSteps.length === 0 && (
            <p className="text-center py-8 text-sm text-[var(--color-text-muted)] italic">
              No steps executed yet. Click &quot;Run it live&quot; to trigger a run or switch to Recorded Replay.
            </p>
          )}
        </div>
      </section>

      {/* (c) Evidence List Built Only from IDs Present in Step Refs */}
      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-2 border-b border-gray-100 pb-3">
          <FileCheck2 className="h-5 w-5 text-[var(--color-accent)]" />
          <h2 className="text-lg font-bold text-[var(--color-primary)]">Evidence Reference Graph</h2>
        </div>
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">
          Identifiers attested across pipeline steps and verified gateway payloads.
        </p>

        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {evidenceList.map((item) => (
            <div key={`${item.key}-${item.id}`} className="rounded-lg border border-gray-200 bg-white p-3 shadow-2xs">
              <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                {item.key} ({item.stepName})
              </span>
              <p className="mt-1 font-mono text-xs font-semibold text-[var(--color-primary)] truncate" title={item.id}>
                {item.id}
              </p>
            </div>
          ))}
          {evidenceList.length === 0 && (
            <p className="text-xs italic text-[var(--color-text-muted)] col-span-3">No references available.</p>
          )}
        </div>
      </section>

      {/* (d) Why Card */}
      <WhyCard auditTrail={effectiveAuditTrail} isLoading={auditLoading && mode === 'live'} />
    </div>
  );
};

export default LiveRoute;
