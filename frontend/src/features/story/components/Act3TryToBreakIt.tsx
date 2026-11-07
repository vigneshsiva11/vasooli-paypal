import React, { useState, useEffect } from 'react';
import {
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Clock,
  Terminal,
  Play,
  RefreshCw,
} from 'lucide-react';
import type { ScenarioAttemptResponse, AgentAuditEntry } from '@/features/agent/api/agentApi';
import { getDecisionColor, formatPolicyClause, parseRetryAfter } from '@/features/agent/utils/agentUtils';
import { executeScenarioAttemptWithFallback } from '../utils/storyData';
import { getScenarioAttemptCaption } from '../utils/storyCaptions';

interface Props {
  slideIndex: number;
  onScenarioExecuted?: (caption: string) => void;
}

interface ScenarioMeta {
  id: string;
  name: string;
  category: string;
  description: string;
  expectedOutcome: string;
}

const TARGET_SCENARIOS: ScenarioMeta[] = [
  {
    id: 'retry_on_9500',
    name: 'Retry on a do-not-retry decline',
    category: 'Processor Code Refusal',
    description: 'Attempt automated payment retry when processor response code 9500 indicates do-not-retry (suspected fraud).',
    expectedOutcome: 'blocked',
  },
  {
    id: 'opted_out_contact',
    name: 'An opted-out contact',
    category: 'Customer Consent Invariant',
    description: 'Attempt customer outreach message when customer is marked as opted-out on immutable do-not-contact list.',
    expectedOutcome: 'blocked',
  },
  {
    id: '1500_without_approval',
    name: 'A $1,500 spend',
    category: 'Authorization Ceiling',
    description: 'Attempt autonomous execution for $1,500 USD which exceeds the $1,000 never-auto threshold.',
    expectedOutcome: 'manual_review',
  },
];

export const Act3TryToBreakIt: React.FC<Props> = ({ slideIndex, onScenarioExecuted }) => {
  const [activeScenarioId, setActiveScenarioId] = useState<string>('retry_on_9500');
  const [results, setResults] = useState<
    Record<string, { response: ScenarioAttemptResponse; auditRow: AgentAuditEntry }>
  >({});
  const [runningMap, setRunningMap] = useState<Record<string, boolean>>({});
  const [errorMap, setErrorMap] = useState<Record<string, string>>({});
  const [rateLimitSeconds, setRateLimitSeconds] = useState<number | null>(null);

  // Sync active scenario with slideIndex
  useEffect(() => {
    if (slideIndex === 11) setActiveScenarioId('retry_on_9500');
    else if (slideIndex === 12) setActiveScenarioId('opted_out_contact');
    else if (slideIndex === 13) setActiveScenarioId('1500_without_approval');
  }, [slideIndex]);

  // Execute scenario live via POST /agent/attempt
  const runScenario = async (scenarioId: string) => {
    setRunningMap((prev) => ({ ...prev, [scenarioId]: true }));
    setErrorMap((prev) => ({ ...prev, [scenarioId]: '' }));

    try {
      const data = await executeScenarioAttemptWithFallback(scenarioId);
      setResults((prev) => ({ ...prev, [scenarioId]: data }));
      if (onScenarioExecuted) {
        onScenarioExecuted(getScenarioAttemptCaption(data.response));
      }
    } catch (err: any) {
      if (err?.status === 429) {
        const secs = parseRetryAfter(err);
        setRateLimitSeconds(secs);
        setErrorMap((prev) => ({
          ...prev,
          [scenarioId]: `Rate limit reached (429). Please wait ${secs}s before trying again.`,
        }));
      } else {
        const msg = err?.payload?.detail || err?.message || 'Scenario execution failed.';
        setErrorMap((prev) => ({ ...prev, [scenarioId]: String(msg) }));
      }
    } finally {
      setRunningMap((prev) => ({ ...prev, [scenarioId]: false }));
    }
  };

  // Auto-run active scenario when entering slide if not yet executed
  useEffect(() => {
    if (!results[activeScenarioId] && !runningMap[activeScenarioId] && !errorMap[activeScenarioId]) {
      runScenario(activeScenarioId);
    }
  }, [activeScenarioId]);

  // Countdown timer for 429
  useEffect(() => {
    if (rateLimitSeconds === null || rateLimitSeconds <= 0) return;
    const interval = setInterval(() => {
      setRateLimitSeconds((prev) => {
        if (prev === null || prev <= 1) return null;
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [rateLimitSeconds]);

  const activeScenario =
    TARGET_SCENARIOS.find((s) => s.id === activeScenarioId) || TARGET_SCENARIOS[0];
  const activeResult = results[activeScenarioId];
  const isRunning = runningMap[activeScenarioId];
  const activeError = errorMap[activeScenarioId];

  const decisionConfig = activeResult
    ? getDecisionColor(activeResult.response.verdict || activeResult.response.outcome)
    : null;

  const clause = activeResult
    ? formatPolicyClause(activeResult.response.check, activeResult.response.reason)
    : null;

  return (
    <div className="space-y-6">
      {/* Act Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800 pb-4">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
            Act 3 &bull; Adversarial & Invariant Defense
          </span>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <ShieldAlert className="h-6 w-6 text-cyan-400" />
            Try to break it
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            Simulate hostile actions and perimeter breach attempts against POST /agent/attempt to prove policy boundaries hold.
          </p>
        </div>

        {/* Live Gateway Pill */}
        <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/80 px-3.5 py-2">
          <div className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-mono text-xs text-slate-300">Gateway: POST /agent/attempt</span>
        </div>
      </div>

      {/* 429 Countdown Notice if active */}
      {rateLimitSeconds !== null && rateLimitSeconds > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-500/40 bg-rose-950/30 px-4 py-3 text-xs text-rose-200">
          <Clock className="h-4 w-4 text-rose-400 shrink-0" />
          <span>Gateway rate limit active: cooldown {rateLimitSeconds}s remaining.</span>
        </div>
      )}

      {/* Scenario Selector Pills */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {TARGET_SCENARIOS.map((sc) => {
          const isSelected = activeScenarioId === sc.id;
          const scResult = results[sc.id];
          const colorCfg = scResult ? getDecisionColor(scResult.response.verdict) : null;

          return (
            <button
              key={sc.id}
              type="button"
              onClick={() => setActiveScenarioId(sc.id)}
              className={`rounded-xl border p-4 text-left transition flex flex-col justify-between gap-3 ${
                isSelected
                  ? 'border-cyan-400 bg-cyan-950/40 text-white ring-1 ring-cyan-400/50'
                  : 'border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-cyan-400">
                    {sc.category}
                  </span>
                  {scResult && colorCfg && (
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${colorCfg.badgeClass}`}>
                      {colorCfg.label}
                    </span>
                  )}
                </div>
                <h3 className="font-bold text-sm text-white mt-1">{sc.name}</h3>
                <p className="text-xs text-slate-400 mt-1 line-clamp-2 leading-relaxed">
                  {sc.description}
                </p>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-800/80 text-[11px] font-mono text-slate-400">
                <span>{sc.id}</span>
                <span className="text-cyan-400 hover:underline">Inspect &rarr;</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Main Execution View for Active Scenario */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-6 space-y-6">
        {/* Scenario Header and Trigger */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800 pb-4">
          <div>
            <span className="text-xs font-mono text-cyan-400">Scenario: {activeScenario.id}</span>
            <h3 className="text-lg font-bold text-white mt-0.5">{activeScenario.name}</h3>
            <p className="text-xs text-slate-300 mt-1 max-w-2xl">{activeScenario.description}</p>
          </div>

          <button
            type="button"
            onClick={() => runScenario(activeScenario.id)}
            disabled={isRunning}
            className="flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-bold transition shadow-xs disabled:opacity-50"
          >
            {isRunning ? (
              <>
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                <span>Executing Gateway Attempt...</span>
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5 fill-current" />
                <span>Re-run Live Attempt</span>
              </>
            )}
          </button>
        </div>

        {/* Error message card */}
        {activeError && (
          <div className="flex items-start gap-3 rounded-xl border border-rose-500/40 bg-rose-950/30 p-4 text-xs text-rose-200">
            <AlertTriangle className="h-5 w-5 text-rose-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold">Execution Error</p>
              <p className="mt-0.5 text-slate-300 font-mono">{activeError}</p>
            </div>
          </div>
        )}

        {/* Decision & Policy Clause Display */}
        {activeResult ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* The Decision Card */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 space-y-4">
              <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
                1. Gateway Decision
              </span>
              <div className="flex items-center gap-3">
                <span
                  className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-xl text-sm font-bold border ${
                    decisionConfig?.badgeClass || 'bg-slate-800 text-white'
                  }`}
                >
                  {decisionConfig?.colorName === 'green' ? (
                    <CheckCircle2 className="h-4 w-4" />
                  ) : decisionConfig?.colorName === 'amber' ? (
                    <AlertTriangle className="h-4 w-4" />
                  ) : (
                    <XCircle className="h-4 w-4" />
                  )}
                  {decisionConfig?.label || 'EVALUATED'}
                </span>
                <span className="font-mono text-xs text-slate-400">
                  Target tool: <strong className="text-white">{activeResult.response.tool}</strong>
                </span>
              </div>

              {/* The Clause Card */}
              <div className="mt-4 pt-4 border-t border-slate-800 space-y-2">
                <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  2. Clause Evaluated
                </span>
                <div className="rounded-lg bg-slate-900 border border-slate-800 p-3.5">
                  <p className="font-bold text-xs text-white">
                    {clause?.checkTitle || activeResult.response.check || 'Policy Check'}
                  </p>
                  <p className="text-xs text-cyan-300 mt-1 leading-relaxed">
                    {clause?.plainEnglish || activeResult.response.reason}
                  </p>
                </div>
              </div>
            </div>

            {/* The Audit Row Card */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 space-y-3 font-mono text-xs">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <span className="font-sans text-xs font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                  <Terminal className="h-4 w-4" />
                  3. Audit Row
                </span>
                <span className="text-[10px] text-slate-500">Live Immutable Log</span>
              </div>

              <div className="space-y-1.5 text-slate-300 text-[11px]">
                <div className="flex justify-between">
                  <span className="text-slate-500">Agent ID:</span>
                  <span className="text-white">{activeResult.auditRow.agent_id}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Scenario/Event:</span>
                  <span className="text-cyan-300">{activeResult.auditRow.event_or_scenario}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Tool Invocated:</span>
                  <span className="text-white">{activeResult.auditRow.tool}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Decision Outcome:</span>
                  <span className="text-rose-400 font-bold">{activeResult.auditRow.outcome}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Policy Reason:</span>
                  <span className="text-amber-300">{activeResult.auditRow.reason}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Invariant Check:</span>
                  <span className="text-white">{activeResult.auditRow.check || 'none'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Timestamp:</span>
                  <span className="text-slate-400">{activeResult.auditRow.at}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Client Key Hash:</span>
                  <span className="text-slate-400">{activeResult.auditRow.client_key_hash}</span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center p-8 text-slate-400 gap-2">
            <RefreshCw className="h-5 w-5 animate-spin text-cyan-400" />
            <span>Connecting to gateway...</span>
          </div>
        )}
      </div>
    </div>
  );
};
