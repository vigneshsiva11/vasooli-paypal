import React, { useState, useEffect } from 'react';
import {
  SlidersHorizontal,
  CheckCircle2,
  XCircle,
  Sparkles,
  ShieldCheck,
  Fingerprint,
  FileText,
  KeyRound,
  RefreshCw,
} from 'lucide-react';
import { EXAMPLE_CHIPS, HARD_CAPS, evaluateHardCaps } from '@/features/rules/utils/rulesUtils';
import type { RulePreviewResponse } from '@/features/rules/api/rulesApi';
import { loadRulePreviewWithFallback } from '../utils/storyData';

interface Props {
  slideIndex: number;
  onPreviewLoaded?: (preview: RulePreviewResponse) => void;
}

export const Act1SayTheRules: React.FC<Props> = ({ slideIndex, onPreviewLoaded }) => {
  const [selectedChipId, setSelectedChipId] = useState<string>('conservative');
  const [preview, setPreview] = useState<RulePreviewResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Sync selected chip when slide index changes
  useEffect(() => {
    if (slideIndex === 0) setSelectedChipId('conservative');
    else if (slideIndex === 1) setSelectedChipId('conservative');
    else if (slideIndex === 2) setSelectedChipId('active-retry');
  }, [slideIndex]);

  // Load preview data whenever chip changes
  useEffect(() => {
    let isCancelled = false;
    setLoading(true);

    const chip = EXAMPLE_CHIPS.find((c) => c.id === selectedChipId) || EXAMPLE_CHIPS[0];

    loadRulePreviewWithFallback(chip.text, chip.id)
      .then((res) => {
        if (!isCancelled) {
          setPreview(res);
          setLoading(false);
          if (onPreviewLoaded) onPreviewLoaded(res);
        }
      })
      .catch(() => {
        if (!isCancelled) {
          setLoading(false);
        }
      });

    return () => {
      isCancelled = true;
    };
  }, [selectedChipId, onPreviewLoaded]);

  const activeChip = EXAMPLE_CHIPS.find((c) => c.id === selectedChipId) || EXAMPLE_CHIPS[0];
  const capEvaluation = preview ? evaluateHardCaps(preview.proposed_parameters, preview.errors) : null;
  const paramKeys = preview ? Object.keys(preview.proposed_parameters || {}) : [];

  return (
    <div className="space-y-6">
      {/* Act Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800 pb-4">
        <div>
          <span className="text-xs font-bold uppercase tracking-wider text-cyan-400">
            Act 1 &bull; Natural Language Governance
          </span>
          <h2 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <SlidersHorizontal className="h-6 w-6 text-cyan-400" />
            Say the rules
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            Plain-English policies parsed into deterministic bounds with verbatim source phrase grounding.
          </p>
        </div>

        {/* Prospective Fingerprint Badge */}
        {preview && (
          <div className="rounded-xl border border-slate-700/80 bg-slate-800/80 px-4 py-2.5 text-right">
            <span className="block text-[11px] font-semibold text-slate-400 flex items-center justify-end gap-1">
              <Fingerprint className="h-3.5 w-3.5 text-cyan-400" />
              Fingerprint
            </span>
            <span className="font-mono text-xs font-bold text-cyan-300">
              {preview.prospective_fingerprint || preview.current_fingerprint || 'Baseline In Force'}
            </span>
          </div>
        )}
      </div>

      {/* Cached Example Chips */}
      <div>
        <label className="block text-xs font-semibold text-slate-400 mb-2">
          Cached Example Chips (instant evaluation from POST /rules/preview or bundled copy):
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
          {EXAMPLE_CHIPS.map((chip) => {
            const isSelected = selectedChipId === chip.id;
            return (
              <button
                key={chip.id}
                type="button"
                onClick={() => setSelectedChipId(chip.id)}
                className={`rounded-xl border p-3 text-left transition ${
                  isSelected
                    ? 'border-cyan-400 bg-cyan-950/40 text-white ring-1 ring-cyan-400/50'
                    : 'border-slate-800 bg-slate-900/60 hover:border-slate-700 text-slate-300'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-xs text-white">{chip.title}</span>
                  <Sparkles className={`h-3 w-3 ${isSelected ? 'text-cyan-400' : 'text-slate-500'}`} />
                </div>
                <p className="mt-1.5 text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                  {chip.description}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Rule Request Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-sm space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
            <FileText className="h-4 w-4" />
            Active Rule Request
          </span>
          <span className="text-xs font-mono text-slate-400">
            {activeChip.text.length} characters
          </span>
        </div>
        <div className="rounded-lg bg-slate-950/70 border border-slate-800 p-3.5 font-mono text-sm text-slate-200 leading-relaxed">
          &ldquo;{activeChip.text}&rdquo;
        </div>
      </div>

      {/* Parsed Parameters with Verbatim Source Phrases & Hard-Cap Result */}
      {loading ? (
        <div className="flex items-center justify-center p-8 text-slate-400 gap-2">
          <RefreshCw className="h-5 w-5 animate-spin text-cyan-400" />
          <span>Evaluating rulebook proposal...</span>
        </div>
      ) : preview ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Grounded Parameters Table */}
          <div className="lg:col-span-2 rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <KeyRound className="h-4 w-4 text-cyan-400" />
                Parsed Parameters & Verbatim Source Phrases
              </h3>
              <span className="text-xs text-slate-400">
                {paramKeys.length} parameter{paramKeys.length === 1 ? '' : 's'} extracted
              </span>
            </div>

            {paramKeys.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border border-slate-800">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-950/80 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2.5 px-3 font-semibold">Parameter</th>
                      <th className="py-2.5 px-3 font-semibold">Proposed Value</th>
                      <th className="py-2.5 px-3 font-semibold">Verbatim Source Phrase</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 font-mono">
                    {paramKeys.map((key) => {
                      const cap = HARD_CAPS[key];
                      const label = cap?.label || key;
                      const val = preview.proposed_parameters[key];
                      const sourcePhrase = preview.parameter_sources?.[key] || '—';

                      return (
                        <tr key={key} className="hover:bg-slate-800/40">
                          <td className="py-2.5 px-3 font-sans font-medium text-slate-200">
                            <div>{label}</div>
                            <span className="text-[10px] text-slate-500 font-mono">{key}</span>
                          </td>
                          <td className="py-2.5 px-3 font-bold text-cyan-300">
                            {val != null ? String(val) : '—'} {cap?.unit && `(${cap.unit})`}
                          </td>
                          <td className="py-2.5 px-3 italic font-sans text-amber-300">
                            {sourcePhrase !== '—' ? `"${sourcePhrase}"` : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-lg bg-slate-950/60 p-4 text-center text-xs text-slate-400 border border-slate-800">
                {preview.valid ? 'No parameter changes proposed.' : 'Rules rejected: untrusted commands or immutable currency parameters.'}
              </div>
            )}
          </div>

          {/* Hard-Cap Safety Invariants Result Card */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-cyan-400" />
              Hard-Cap Safety Evaluation
            </h3>

            {capEvaluation && capEvaluation.passed && preview.valid ? (
              <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/30 p-4 text-xs text-emerald-200 space-y-2">
                <div className="flex items-center gap-2 font-bold text-emerald-300">
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                  PASS: Safety limits respected
                </div>
                <p className="text-slate-300 leading-relaxed">
                  All proposed parameters fall within hard minimum/maximum boundaries. No customer opt-out bypasses detected.
                </p>
                <div className="mt-2 pt-2 border-t border-emerald-900/60 font-mono text-[11px] text-emerald-400">
                  Fingerprint attested: {preview.prospective_fingerprint || preview.current_fingerprint}
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-rose-500/40 bg-rose-950/30 p-4 text-xs text-rose-200 space-y-2">
                <div className="flex items-center gap-2 font-bold text-rose-300">
                  <XCircle className="h-4 w-4 shrink-0" />
                  REJECTED: Policy violation
                </div>
                <ul className="list-disc pl-4 space-y-1 text-slate-300">
                  {preview.errors && preview.errors.length > 0 ? (
                    preview.errors.map((err, i) => <li key={i}>{err}</li>)
                  ) : (
                    <li>Safety limits exceeded or unsupported instruction.</li>
                  )}
                </ul>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};

