import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ShieldAlert,
  Bot,
  AlertTriangle,
  Clock,
  Radio,
  Server,
  RefreshCw,
  Terminal,
  FileCode,
} from 'lucide-react';
import {
  getScenarios,
  attemptScenario,
  getAgentAudit,
  type Scenario,
  type ScenarioAttemptResponse,
  type AgentAuditEntry,
} from '../api/agentApi';
import {
  getDecisionColor,
  formatPolicyClause,
  parseRetryAfter,
  formatCountdown,
} from '../utils/agentUtils';
import { getApiConfig, ApiError } from '@/api/client';

export const AgentRoute: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [latestAttempt, setLatestAttempt] = useState<ScenarioAttemptResponse | null>(null);
  const [lastRequestPayload, setLastRequestPayload] = useState<{ scenario_id: string; tool?: string; name?: string } | null>(null);

  // Rate-limit 429 countdown timer state
  const [retryAfterSeconds, setRetryAfterSeconds] = useState<number | null>(null);

  useEffect(() => {
    if (retryAfterSeconds === null || retryAfterSeconds <= 0) return;
    const interval = setInterval(() => {
      setRetryAfterSeconds((prev) => {
        if (prev === null || prev <= 1) return null;
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [retryAfterSeconds]);

  // Query 1: Scenario list (7 scenarios)
  const {
    data: scenarios,
    isLoading: scenariosLoading,
    error: scenariosError,
    refetch: refetchScenarios,
  } = useQuery<Scenario[]>({
    queryKey: ['agent-scenarios'],
    queryFn: ({ signal }) => getScenarios(signal),
    staleTime: 1000 * 60 * 10,
  });

  // Query 2: Live audit feed (polled every 5 seconds)
  const {
    data: auditEntries,
    isLoading: auditLoading,
    error: auditError,
    refetch: refetchAudit,
  } = useQuery<AgentAuditEntry[]>({
    queryKey: ['agent-audit'],
    queryFn: ({ signal }) => getAgentAudit(20, signal),
    refetchInterval: 5000,
  });

  // Mutation: Attempt scenario
  const attemptMutation = useMutation({
    mutationFn: (scenarioId: string) => attemptScenario(scenarioId),
    onSuccess: (data, scenarioId) => {
      setLatestAttempt(data);
      const matched = scenarios?.find((s) => s.scenario_id === scenarioId);
      setLastRequestPayload({
        scenario_id: scenarioId,
        tool: data.tool || matched?.tool,
        name: data.name || matched?.name,
      });
      // Invalidate audit query to show the new attempt immediately
      queryClient.invalidateQueries({ queryKey: ['agent-audit'] });
    },
    onError: (err: any) => {
      if (err instanceof ApiError && err.status === 429) {
        const seconds = parseRetryAfter(err);
        setRetryAfterSeconds(seconds);
      }
    },
  });

  const handleRunScenario = (scenario: Scenario) => {
    if (retryAfterSeconds && retryAfterSeconds > 0) return;
    setSelectedScenarioId(scenario.scenario_id);
    setLastRequestPayload({
      scenario_id: scenario.scenario_id,
      tool: scenario.tool,
      name: scenario.name,
    });
    attemptMutation.mutate(scenario.scenario_id);
  };

  const { baseUrl } = getApiConfig();
  const mcpEndpointUrl = baseUrl ? `${baseUrl}/mcp` : '/mcp';

  // Determine error states
  const mutationError = attemptMutation.error;
  const is429 = (mutationError instanceof ApiError && mutationError.status === 429) || (retryAfterSeconds !== null && retryAfterSeconds > 0);
  const is503 = mutationError instanceof ApiError && mutationError.status === 503;
  const isNetworkError = mutationError && !(mutationError instanceof ApiError);

  return (
    <div className="space-y-6">
      {/* 1. Mandatory Sandbox Dry-Run Banner */}
      <div
        role="alert"
        className="flex items-center gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3.5 text-amber-900 shadow-xs"
      >
        <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
        <span className="text-sm font-semibold tracking-wide">
          Sandbox. Nothing here moves money: attempts are evaluated by the real policy layer in dry-run.
        </span>
      </div>

      {/* Header section */}
      <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--color-primary)]">
            Try to break it
          </h1>
          <p className="text-sm text-[var(--color-text-muted)]">
            Adversarial testing sandbox for the agent gateway. Exercise policy guardrails against attack scenarios.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-1.5 rounded-full border border-emerald-200">
          <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span>Gateway Active (Dry-Run Guarded)</span>
        </div>
      </div>

      {/* 2. Connect your own agent Box */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-[var(--color-accent,#4338ca)]" />
              <h2 className="text-base font-semibold text-[var(--color-primary)]">
                Connect your own agent
              </h2>
            </div>
            <p className="text-xs text-[var(--color-text-muted)]">
              Mount your autonomous agent over Model Context Protocol (MCP) streamable HTTP.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5 rounded-lg bg-gray-50 border border-gray-200 px-3 py-1.5 font-mono text-xs text-gray-700">
              <Terminal className="h-3.5 w-3.5 text-gray-500" />
              <span className="font-semibold text-gray-900">MCP Path:</span>
              <span>{mcpEndpointUrl}</span>
            </div>
            <div className="rounded-lg bg-indigo-50 border border-indigo-200 px-3 py-1.5 text-xs font-medium text-indigo-700">
              token available on request
            </div>
          </div>
        </div>
      </div>

      {/* 3. Error Alerts (429, 503, Network Errors) */}
      {is429 && (
        <div className="rounded-xl border border-rose-300 bg-rose-50 p-4 text-rose-900 shadow-xs flex items-start gap-3">
          <Clock className="h-5 w-5 text-rose-600 shrink-0 mt-0.5" />
          <div className="space-y-1 text-sm">
            <div className="font-semibold">Rate limit active (HTTP 429 Too Many Requests)</div>
            <div className="text-xs text-rose-700">
              Please wait{' '}
              <span className="font-mono font-bold text-rose-900">
                {formatCountdown(retryAfterSeconds || 60)}
              </span>{' '}
              before submitting another scenario attempt.
            </div>
            {mutationError?.message && (
              <div className="font-mono text-xs bg-rose-100/60 p-2 rounded border border-rose-200 break-words mt-2">
                {mutationError.message}
              </div>
            )}
          </div>
        </div>
      )}

      {is503 && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-900 shadow-xs flex items-start gap-3">
          <Server className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="space-y-1 text-sm">
            <div className="font-semibold">Service Unavailable (HTTP 503)</div>
            <p className="text-xs text-amber-700">
              The agent gateway is currently unavailable or agent token configuration is missing in production.
            </p>
            {mutationError?.message && (
              <div className="font-mono text-xs bg-amber-100/60 p-2 rounded border border-amber-200 break-words mt-2">
                {mutationError.message}
              </div>
            )}
          </div>
        </div>
      )}

      {isNetworkError && (
        <div className="rounded-xl border border-gray-300 bg-gray-50 p-4 text-gray-900 shadow-xs flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-gray-600 shrink-0 mt-0.5" />
          <div className="space-y-1 text-sm">
            <div className="font-semibold">Network Connection Error</div>
            <p className="text-xs text-gray-700">
              Failed to connect to the agent gateway. Verify backend server availability.
            </p>
            {mutationError?.message && (
              <div className="font-mono text-xs bg-gray-200/60 p-2 rounded border border-gray-300 break-words mt-2">
                {String(mutationError.message)}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 4. Seven Scenario Buttons */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Select a Scenario to Attempt
          </h2>
          {scenariosLoading && (
            <span className="text-xs text-gray-500 flex items-center gap-1">
              <RefreshCw className="h-3 w-3 animate-spin" /> Loading scenarios...
            </span>
          )}
        </div>

        {scenariosError ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 flex items-center justify-between">
            <span>Failed to load catalogue scenarios.</span>
            <button
              onClick={() => refetchScenarios()}
              className="text-xs font-semibold underline hover:text-rose-900"
            >
              Retry
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {scenarios?.map((scenario) => {
              const isSelected = selectedScenarioId === scenario.scenario_id;
              const isRunning = attemptMutation.isPending && isSelected;
              const expColor = getDecisionColor(scenario.expected_outcome);

              return (
                <button
                  key={scenario.scenario_id}
                  onClick={() => handleRunScenario(scenario)}
                  disabled={isRunning || (retryAfterSeconds !== null && retryAfterSeconds > 0)}
                  className={`group relative flex flex-col text-left rounded-xl border p-4 transition-all duration-150 ${
                    isSelected
                      ? 'border-indigo-600 ring-2 ring-indigo-600/20 bg-white shadow-sm'
                      : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-xs'
                  } ${
                    retryAfterSeconds && retryAfterSeconds > 0
                      ? 'opacity-60 cursor-not-allowed'
                      : 'cursor-pointer'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2 w-full">
                    <span className="font-semibold text-sm text-[var(--color-primary)] group-hover:text-indigo-600 transition-colors">
                      {scenario.name}
                    </span>
                    <span
                      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${expColor.badgeClass}`}
                    >
                      {scenario.expected_outcome}
                    </span>
                  </div>

                  <p className="text-xs text-[var(--color-text-muted)] line-clamp-2 mb-3">
                    {scenario.description}
                  </p>

                  <div className="mt-auto pt-2 border-t border-gray-100 flex items-center justify-between w-full text-[11px] text-gray-500 font-mono">
                    <span>Tool: {scenario.tool}</span>
                    {isRunning ? (
                      <span className="flex items-center gap-1 text-indigo-600 font-sans font-semibold">
                        <RefreshCw className="h-3 w-3 animate-spin" /> Evaluating...
                      </span>
                    ) : (
                      <span className="text-indigo-600 group-hover:translate-x-0.5 transition-transform font-sans font-medium">
                        Run &rarr;
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 5. Attempt Result View */}
      {latestAttempt && (
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-gray-100 gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                Latest Gateway Attempt Result
              </div>
              <h3 className="text-lg font-bold text-[var(--color-primary)] flex items-center gap-2">
                <span>{latestAttempt.name}</span>
                <span className="font-mono text-xs font-normal text-gray-500">
                  ({latestAttempt.scenario_id})
                </span>
              </h3>
            </div>

            {/* The Gateway's Decision Badge */}
            <div>
              {(() => {
                const decisionConf = getDecisionColor(latestAttempt.outcome || latestAttempt.verdict);
                return (
                  <div
                    className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border font-bold text-xs tracking-wide shadow-xs ${decisionConf.badgeClass}`}
                  >
                    <span className={`h-2.5 w-2.5 rounded-full ${decisionConf.dotClass}`} />
                    <span>DECISION: {decisionConf.label}</span>
                  </div>
                );
              })()}
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* The Request */}
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase tracking-wider">
                <FileCode className="h-3.5 w-3.5 text-gray-500" />
                <span>The Request</span>
              </div>
              <div className="rounded-lg bg-gray-900 p-4 text-xs font-mono text-gray-100 overflow-x-auto shadow-inner">
                <pre className="whitespace-pre-wrap">
                  {JSON.stringify(
                    {
                      scenario_id: lastRequestPayload?.scenario_id,
                      tool: lastRequestPayload?.tool,
                      target_name: lastRequestPayload?.name,
                    },
                    null,
                    2
                  )}
                </pre>
              </div>
            </div>

            {/* The Clause & The Active Rulebook Fingerprint */}
            <div className="space-y-4">
              {/* The Clause */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  <ShieldAlert className="h-3.5 w-3.5 text-gray-500" />
                  <span>The Clause (Policy Check & Reason)</span>
                </div>
                {(() => {
                  const clause = formatPolicyClause(latestAttempt.check, latestAttempt.reason);
                  const decisionConf = getDecisionColor(latestAttempt.outcome || latestAttempt.verdict);
                  return (
                    <div className={`rounded-lg border p-4 ${decisionConf.cardBorderClass} space-y-2`}>
                      <div className="font-semibold text-sm text-[var(--color-primary)]">
                        {clause.checkTitle}
                      </div>
                      <p className="text-xs text-gray-700 leading-relaxed font-sans">
                        {clause.plainEnglish}
                      </p>
                      <div className="pt-2 border-t border-gray-200/50 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono text-gray-600">
                        <span>Check: <strong>{latestAttempt.check || 'None'}</strong></span>
                        <span>Reason: <strong>{latestAttempt.reason}</strong></span>
                      </div>
                    </div>
                  );
                })()}
              </div>

              {/* The Active Rulebook Fingerprint */}
              <div className="space-y-1.5">
                <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Active Rulebook Fingerprint
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-200 px-3.5 py-2.5 font-mono text-xs text-gray-800 break-all flex items-center justify-between">
                  <span>{latestAttempt.rulebook_fingerprint || 'None (rejected before policy evaluation)'}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 6. Live Audit Feed */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio className="h-4 w-4 text-emerald-600 animate-pulse" />
            <h2 className="text-base font-bold text-[var(--color-primary)]">
              Live Gateway Audit Feed
            </h2>
            <span className="text-xs text-gray-500 font-medium">
              (polled every 5s)
            </span>
          </div>
          <button
            onClick={() => refetchAudit()}
            className="flex items-center gap-1.5 text-xs font-semibold text-gray-600 hover:text-gray-900 transition-colors p-1"
            title="Refresh feed"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${auditLoading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        {auditError ? (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-xs text-rose-800">
            Failed to load live audit feed.
          </div>
        ) : !auditEntries || auditEntries.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 p-8 text-center text-xs text-gray-500">
            No gateway audit events recorded yet. Run a scenario above to observe real-time audit records.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs text-gray-600">
              <thead className="bg-gray-50/80 text-[11px] font-semibold uppercase tracking-wider text-gray-500 border-b border-gray-200">
                <tr>
                  <th className="py-2.5 px-3">Time</th>
                  <th className="py-2.5 px-3">Tool</th>
                  <th className="py-2.5 px-3">Scenario / Target</th>
                  <th className="py-2.5 px-3">Decision</th>
                  <th className="py-2.5 px-3">Clause Reason</th>
                  <th className="py-2.5 px-3">Rulebook</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 font-mono">
                {auditEntries.map((entry, idx) => {
                  const decisionConf = getDecisionColor(entry.outcome || entry.verdict);
                  const timeFormatted = new Date(entry.at).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                  });

                  return (
                    <tr key={`${entry.at}-${entry.args_hash}-${idx}`} className="hover:bg-gray-50/50">
                      <td className="py-2.5 px-3 whitespace-nowrap text-gray-500">
                        {timeFormatted}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap text-indigo-700 font-semibold">
                        {entry.tool}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap text-gray-900 font-medium">
                        {entry.event_or_scenario}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${decisionConf.badgeClass}`}
                        >
                          {decisionConf.label}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-gray-700 font-sans max-w-xs truncate" title={entry.reason}>
                        {entry.reason}
                        {entry.check && (
                          <span className="text-[10px] font-mono text-gray-500 block">
                            [{entry.check}]
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap text-gray-500 text-[11px]">
                        {entry.rulebook_fingerprint ? (
                          <span title={entry.rulebook_fingerprint}>
                            {entry.rulebook_fingerprint.slice(0, 16)}...
                          </span>
                        ) : (
                          <span className="text-gray-400">None</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default AgentRoute;
