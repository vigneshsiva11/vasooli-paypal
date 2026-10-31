import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { SlidersHorizontal, Sparkles, Send, AlertTriangle, AlertCircle, Clock } from 'lucide-react';
import {
  getActiveRulebook,
  getRulesHistory,
  getRulesPreview,
  type RulePreviewResponse,
} from '../api/rulesApi';
import {
  applyRulesProposal,
  revertRulesToBaseline,
} from '../adminClient';
import {
  EXAMPLE_CHIPS,
  parseRulesError,
  type RulesErrorState,
} from '../utils/rulesUtils';
import { ActiveRulebookBanner } from './ActiveRulebookBanner';
import { AdminUnlockSection } from './AdminUnlockSection';
import { RulePreviewResults } from './RulePreviewResults';
import { RulebookHistoryList } from './RulebookHistoryList';

export const RulesRoute: React.FC = () => {
  const queryClient = useQueryClient();

  // Rule text input state (500 max characters)
  const [ruleText, setRuleText] = useState('');

  // Admin token kept in React state ONLY (never localStorage, sessionStorage, cookies or URLs; cleared on reload)
  const [adminToken, setAdminToken] = useState<string>('');
  const [isAdminUnlocked, setIsAdminUnlocked] = useState<boolean>(false);

  // Preview & Error states
  const [previewResult, setPreviewResult] = useState<RulePreviewResponse | null>(null);
  const [isPreviewing, setIsPreviewing] = useState<boolean>(false);
  const [previewError, setPreviewError] = useState<RulesErrorState | null>(null);
  const [retryCountdown, setRetryCountdown] = useState<number | null>(null);
  const countdownTimerRef = useRef<any>(null);

  // Apply & Revert states
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const [isReverting, setIsReverting] = useState<boolean>(false);
  const [revertMessage, setRevertMessage] = useState<string | null>(null);
  const [revertError, setRevertError] = useState<string | null>(null);

  // Fetch active rulebook and history
  const {
    data: activeRulebook,
    isLoading: isActiveLoading,
    refetch: refetchActive,
  } = useQuery({
    queryKey: ['rules', 'active'],
    queryFn: ({ signal }) => getActiveRulebook(signal),
  });

  const {
    data: history = [],
    isLoading: isHistoryLoading,
    refetch: refetchHistory,
  } = useQuery({
    queryKey: ['rules', 'history'],
    queryFn: ({ signal }) => getRulesHistory(signal),
  });

  // Handle #history anchor scrolling
  useEffect(() => {
    if (window.location.hash === '#history') {
      const el = document.getElementById('history');
      if (el) {
        setTimeout(() => el.scrollIntoView({ behavior: 'smooth' }), 100);
      }
    }
  }, [window.location.hash]);

  // Countdown timer for 429 rate limit
  useEffect(() => {
    if (retryCountdown !== null && retryCountdown > 0) {
      countdownTimerRef.current = setInterval(() => {
        setRetryCountdown((prev) => {
          if (prev === null || prev <= 1) {
            clearInterval(countdownTimerRef.current);
            return null;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => {
      if (countdownTimerRef.current) {
        clearInterval(countdownTimerRef.current);
      }
    };
  }, [retryCountdown]);

  // Handle preview request
  const handlePreview = async () => {
    if (!ruleText.trim() || isPreviewing || (retryCountdown !== null && retryCountdown > 0)) {
      return;
    }

    setIsPreviewing(true);
    setPreviewError(null);
    setApplyMessage(null);
    setApplyError(null);

    try {
      const res = await getRulesPreview(ruleText.trim());
      setPreviewResult(res);
    } catch (err: any) {
      const parsedErr = parseRulesError(err);
      setPreviewError(parsedErr);
      if (parsedErr.type === '429' && parsedErr.retryAfterSeconds) {
        setRetryCountdown(parsedErr.retryAfterSeconds);
      }
    } finally {
      setIsPreviewing(false);
    }
  };

  // Handle Example Chip selection
  const handleSelectExample = (chipText: string) => {
    setRuleText(chipText);
    setPreviewError(null);
    setApplyMessage(null);
    setApplyError(null);
  };

  // Handle Admin Unlock & Lock (in-memory only)
  const handleAdminUnlock = (token: string) => {
    setAdminToken(token);
    setIsAdminUnlocked(true);
    setApplyError(null);
    setRevertError(null);
  };

  const handleAdminLock = () => {
    setAdminToken('');
    setIsAdminUnlocked(false);
    setApplyError(null);
    setRevertError(null);
  };

  // Handle Apply Rulebook proposal
  const handleApply = async (previewId: string, expectedParentFingerprint: string) => {
    if (!adminToken || !isAdminUnlocked) return;

    setIsApplying(true);
    setApplyMessage(null);
    setApplyError(null);

    try {
      const res = await applyRulesProposal(adminToken, previewId, expectedParentFingerprint);
      setApplyMessage(`Rulebook proposal applied successfully! New active fingerprint: ${res.fingerprint}`);
      await Promise.all([refetchActive(), refetchHistory()]);
      queryClient.invalidateQueries({ queryKey: ['rules'] });
    } catch (err: any) {
      setApplyError(err?.message || 'Failed to apply rulebook proposal.');
    } finally {
      setIsApplying(false);
    }
  };

  // Handle Revert to baseline
  const handleRevert = async () => {
    if (!adminToken || !isAdminUnlocked) return;

    setIsReverting(true);
    setRevertMessage(null);
    setRevertError(null);

    try {
      const res = await revertRulesToBaseline(adminToken);
      setRevertMessage(`USD rulebook reverted to code baseline. Active fingerprint: ${res.active_fingerprint}`);
      await Promise.all([refetchActive(), refetchHistory()]);
      queryClient.invalidateQueries({ queryKey: ['rules'] });
    } catch (err: any) {
      setRevertError(err?.message || 'Failed to revert rulebook to baseline.');
    } finally {
      setIsReverting(false);
    }
  };

  return (
    <div className="space-y-6 pb-16">
      {/* Page Header */}
      <header className="flex flex-col gap-1">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-[var(--color-accent)]">
          Merchant Policy Control
        </p>
        <h1 className="text-3xl font-bold tracking-tight text-[var(--color-primary)]">
          Rules & Policy Configuration
        </h1>
        <p className="max-w-3xl text-sm text-[var(--color-text-muted)]">
          Propose, validate, and preview plain-English merchant recovery policies under strict hard caps and immutable invariants.
        </p>
      </header>

      {/* Active Rulebook Banner */}
      <ActiveRulebookBanner
        activeRulebook={activeRulebook || null}
        isLoading={isActiveLoading}
        isAdminUnlocked={isAdminUnlocked}
        onRevert={handleRevert}
        isReverting={isReverting}
        revertMessage={revertMessage}
        revertError={revertError}
      />

      {/* Admin Unlock Section */}
      <AdminUnlockSection
        isAdminUnlocked={isAdminUnlocked}
        onUnlock={handleAdminUnlock}
        onLock={handleAdminLock}
      />

      {/* Rules Proposal Composer */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
        <div className="flex items-center justify-between border-b border-gray-100 pb-3">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="h-5 w-5 text-[var(--color-accent)]" />
            <h2 className="text-base font-bold text-[var(--color-primary)]">
              Plain-English Rule Proposal
            </h2>
          </div>
          <span
            className={`font-mono text-xs ${
              ruleText.length >= 500 ? 'font-bold text-red-600' : 'text-[var(--color-text-muted)]'
            }`}
          >
            {ruleText.length} / 500 characters
          </span>
        </div>

        {/* Cached Example Chips */}
        <div>
          <span className="block text-xs font-semibold text-[var(--color-text-muted)] mb-2">
            Cached Example Chips (instant evaluation):
          </span>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {EXAMPLE_CHIPS.map((chip) => {
              const isSelected = ruleText === chip.text;
              return (
                <button
                  key={chip.id}
                  type="button"
                  onClick={() => handleSelectExample(chip.text)}
                  className={`rounded-lg border p-2.5 text-left transition ${
                    isSelected
                      ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 text-[var(--color-primary)]'
                      : 'border-gray-200 bg-gray-50/50 hover:border-gray-300 text-gray-700'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-xs text-[var(--color-primary)]">
                      {chip.title}
                    </span>
                    <Sparkles className="h-3 w-3 text-[var(--color-accent)]" />
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-muted)] line-clamp-2 leading-tight">
                    {chip.description}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        {/* 500-Character Textarea */}
        <div className="relative">
          <textarea
            value={ruleText}
            onChange={(e) => setRuleText(e.target.value.slice(0, 500))}
            placeholder="Describe your desired recovery rules in plain English (e.g. 'contact a customer at most twice, wait 48 hours between contacts, auto authorize below $50')..."
            rows={4}
            maxLength={500}
            className="w-full rounded-lg border border-gray-200 bg-[var(--color-bg-surface)] p-3 text-sm text-[var(--color-primary)] placeholder-gray-400 focus:border-[var(--color-accent)] focus:outline-hidden"
          />
        </div>

        {/* Action Controls */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-1">
          <div className="text-xs text-[var(--color-text-muted)]">
            <span>Rules are evaluated deterministically against hard safety bounds.</span>
          </div>

          <button
            type="button"
            onClick={handlePreview}
            disabled={
              !ruleText.trim() ||
              isPreviewing ||
              (retryCountdown !== null && retryCountdown > 0)
            }
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-5 py-2 text-xs font-bold text-white shadow-sm hover:opacity-95 disabled:opacity-50 transition"
          >
            {isPreviewing ? (
              <>Evaluating Preview...</>
            ) : retryCountdown !== null && retryCountdown > 0 ? (
              <>
                <Clock className="h-3.5 w-3.5 animate-spin" /> Retry in {retryCountdown}s
              </>
            ) : (
              <>
                <Send className="h-3.5 w-3.5" /> Preview Proposal
              </>
            )}
          </button>
        </div>

        {/* Clear States for 429, 503, and Network Errors */}
        {previewError && (
          <div className="mt-4">
            {previewError.type === '429' && (
              <div className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 p-3.5 text-xs text-amber-900">
                <AlertCircle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Rate Limit Exceeded (429)</p>
                  <p className="mt-0.5">
                    {retryCountdown !== null && retryCountdown > 0 ? (
                      <>Please wait <strong className="font-mono text-amber-950">{retryCountdown}s</strong> before retrying rule evaluation.</>
                    ) : (
                      previewError.message
                    )}
                  </p>
                </div>
              </div>
            )}

            {previewError.type === '503' && (
              <div className="flex items-start gap-2.5 rounded-lg border border-orange-200 bg-orange-50 p-3.5 text-xs text-orange-900">
                <AlertTriangle className="h-4 w-4 text-orange-600 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Service Temporarily Unavailable (503)</p>
                  <p className="mt-0.5">Rule parsing is unavailable; the examples still work</p>
                </div>
              </div>
            )}

            {previewError.type === 'network' && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3.5 text-xs text-red-900">
                <AlertCircle className="h-4 w-4 text-red-600 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Network Communication Error</p>
                  <p className="mt-0.5">{previewError.message}</p>
                </div>
              </div>
            )}

            {previewError.type === 'generic' && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3.5 text-xs text-red-900">
                <AlertCircle className="h-4 w-4 text-red-600 shrink-0 mt-0.5" />
                <div>
                  <p className="font-bold">Rule Evaluation Error</p>
                  <p className="mt-0.5">{previewError.message}</p>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Preview Results Section */}
      {previewResult && (
        <RulePreviewResults
          preview={previewResult}
          activeFingerprint={activeRulebook?.fingerprint}
          isAdminUnlocked={isAdminUnlocked}
          onApply={handleApply}
          isApplying={isApplying}
          applyMessage={applyMessage}
          applyError={applyError}
        />
      )}

      {/* Rulebook Version History List */}
      <RulebookHistoryList
        history={history}
        isLoading={isHistoryLoading}
        activeFingerprint={activeRulebook?.fingerprint}
      />
    </div>
  );
};

export default RulesRoute;
