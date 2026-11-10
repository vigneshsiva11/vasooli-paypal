import React, { useState, useRef, useEffect } from 'react';
import {
  Bot,
  Send,
  Sparkles,
  RotateCcw,
  Copy,
  Check,
  StopCircle,
  Loader2,
  Clock,
  Info,
  WifiOff,
} from 'lucide-react';
import {
  PROMPT_STARTERS,
  StudioLlmAdapter,
  parseAiError,
  clearChatState,
  type AiErrorInfo,
  type AssistantChatMessage,
} from '../api/studioAi';
import { isStudioAiEnabled } from '../utils/studioAiCheck';
import { StudioMarkdownRenderer } from './StudioMarkdownRenderer';

export const StudioAssistantPanel: React.FC = () => {
  // If the flag is off, render absolutely nothing assistant-related
  if (!isStudioAiEnabled()) {
    return null;
  }

  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<AssistantChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeError, setActiveError] = useState<AiErrorInfo | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Active countdown timer for 429 rate limit
  useEffect(() => {
    if (countdown === null || countdown <= 0) return;
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [countdown]);

  // Auto-scroll to newest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming, activeError]);

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
  };

  const handleClear = () => {
    handleStop();
    const clean = clearChatState();
    setMessages(clean.messages);
    setPrompt(clean.prompt);
    setActiveError(clean.activeError);
    setCountdown(null);
  };

  const handleCopyAnswer = async (text: string, id: string) => {
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(text);
      }
      setCopiedId(id);
      setTimeout(() => {
        setCopiedId((cur) => (cur === id ? null : cur));
      }, 2000);
    } catch (_) {
      // Graceful fallback if clipboard API is unavailable
    }
  };

  const handleSend = async (userPromptText: string) => {
    const textToSend = userPromptText.trim();
    if (!textToSend || isStreaming) return;

    setActiveError(null);
    const userMsgId = `user_${Date.now()}`;
    const botMsgId = `asst_${Date.now()}`;

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', text: textToSend },
      { id: botMsgId, role: 'assistant', text: '' },
    ]);
    setPrompt('');
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const adapter = new StudioLlmAdapter();
      const handler = adapter.executeTurn(
        {
          input: [
            {
              id: userMsgId,
              kind: 'input',
              type: 'message',
              role: 'user',
              status: 'completed',
              content: [{ type: 'text', text: textToSend }],
            },
          ],
          responseFormat: { type: 'text' },
        },
        { signal: controller.signal }
      );

      let accumulated = '';
      for await (const event of handler.stream) {
        if (event.type === 'TEXT_MESSAGE_CONTENT') {
          accumulated += event.delta;
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === botMsgId ? { ...msg, text: accumulated } : msg
            )
          );
        }
      }

      await handler.complete;

      // Inspect completed message for friendly error mapping
      if (accumulated.includes('Rate limit reached')) {
        const match = accumulated.match(/retry in (\d+)s/i);
        const retrySec = match ? parseInt(match[1], 10) : 60;
        const errInfo: AiErrorInfo = {
          type: 'rate_limit',
          message: accumulated,
          retryAfterSeconds: retrySec,
        };
        setActiveError(errInfo);
        setCountdown(retrySec);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsgId
              ? { ...msg, errorType: 'rate_limit', retryAfterSeconds: retrySec }
              : msg
          )
        );
      } else if (
        accumulated.includes('switched off') ||
        accumulated.includes('busy') ||
        accumulated.includes('upstream error') ||
        accumulated.includes('dashboard still works')
      ) {
        const errInfo: AiErrorInfo = {
          type: 'service_unavailable',
          message: accumulated,
        };
        setActiveError(errInfo);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsgId ? { ...msg, errorType: 'service_unavailable' } : msg
          )
        );
      } else if (accumulated.includes('Network error')) {
        const errInfo: AiErrorInfo = {
          type: 'network',
          message: accumulated,
        };
        setActiveError(errInfo);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsgId ? { ...msg, errorType: 'network' } : msg
          )
        );
      }
    } catch (err: any) {
      if (controller.signal.aborted) {
        // Handled by stop action
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsgId
              ? {
                  ...msg,
                  text: msg.text || 'Checking the ledger stopped.',
                  errorType: 'cancelled',
                }
              : msg
          )
        );
      } else {
        const errInfo = parseAiError(null, null, err);
        setActiveError(errInfo);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMsgId
              ? { ...msg, text: errInfo.message, errorType: errInfo.type }
              : msg
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  };

  return (
    <div
      data-testid="studio-assistant-panel"
      className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5 shadow-xs flex flex-col h-[680px] lg:h-[calc(100vh-140px)] lg:max-h-[880px] w-full min-w-0 max-w-full overflow-hidden"
    >
      {/* 1. Header with title, note, and clear button */}
      <div className="pb-3 border-b border-slate-100 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-1">
          <div className="flex items-center gap-2 min-w-0">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-900 text-white shrink-0">
              <Bot className="h-4 w-4" />
            </div>
            <h2 className="text-sm font-semibold text-slate-900 truncate">
              Ask the ledger
            </h2>
          </div>
          <button
            type="button"
            onClick={handleClear}
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition cursor-pointer"
            aria-label="Clear chat"
            title="Clear chat history"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Clear</span>
          </button>
        </div>
        <p className="text-[11px] text-slate-500 font-normal truncate">
          Read-only. Answers come from the sanitized ledger
        </p>
      </div>

      {/* 2. Messages scroll area */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-4 py-3 pr-1 text-xs">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center p-6 text-slate-400">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 mb-2.5">
              <Sparkles className="h-5 w-5 text-slate-400" />
            </div>
            <p className="text-xs font-medium text-slate-600 mb-1">
              Ask anything about recoveries & risk
            </p>
            <p className="text-[11px] text-slate-400 max-w-xs leading-normal">
              Select a prompt starter below or write a custom query to analyze the sanitized ledger.
            </p>
          </div>
        ) : (
          messages.map((m) => {
            if (m.role === 'user') {
              return (
                <div key={m.id} className="flex flex-col items-end">
                  <div className="ml-auto max-w-[85%] rounded-2xl bg-slate-900 text-white px-4 py-2.5 text-xs shadow-xs break-words [overflow-wrap:anywhere]">
                    {m.text}
                  </div>
                </div>
              );
            }

            // Assistant message
            const isCardThinking = isStreaming && !m.text;

            return (
              <div key={m.id} className="flex flex-col items-start w-full">
                {isCardThinking ? (
                  // Thinking state card
                  <div
                    data-testid="thinking-state"
                    className="w-full rounded-xl bg-slate-50 border border-slate-200 p-3.5 text-xs shadow-xs flex items-center justify-between gap-3"
                  >
                    <div className="flex items-center gap-2.5 text-slate-700 font-medium">
                      <Loader2 className="h-4 w-4 animate-spin text-slate-600 shrink-0" />
                      <span>Checking the ledger</span>
                    </div>
                    <button
                      type="button"
                      onClick={handleStop}
                      className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition cursor-pointer"
                      aria-label="Stop response"
                    >
                      <StopCircle className="h-3.5 w-3.5 text-rose-500 shrink-0" />
                      <span>Stop</span>
                    </button>
                  </div>
                ) : m.errorType === 'rate_limit' ? (
                  // Friendly error card for 429
                  <div
                    data-testid="error-card-429"
                    className="w-full rounded-xl border border-amber-200 bg-amber-50/80 p-3.5 text-xs text-amber-900 space-y-1.5 shadow-xs"
                  >
                    <div className="flex items-center gap-2 font-semibold text-amber-800">
                      <Clock className="h-4 w-4 text-amber-600 shrink-0" />
                      <span>Rate limit reached</span>
                    </div>
                    <p className="text-[11px] leading-relaxed">
                      {countdown !== null && countdown > 0 ? (
                        <>
                          Too many requests. Please wait{' '}
                          <strong className="font-semibold text-amber-900">
                            {countdown}s
                          </strong>{' '}
                          before trying again.
                        </>
                      ) : (
                        'Cooldown complete. You may now ask the ledger.'
                      )}
                    </p>
                  </div>
                ) : m.errorType === 'service_unavailable' ? (
                  // Friendly error card for 503
                  <div
                    data-testid="error-card-503"
                    className="w-full rounded-xl border border-blue-200 bg-blue-50/80 p-3.5 text-xs text-blue-900 space-y-1.5 shadow-xs"
                  >
                    <div className="flex items-center gap-2 font-semibold text-blue-800">
                      <Info className="h-4 w-4 text-blue-600 shrink-0" />
                      <span>Assistant Unavailable</span>
                    </div>
                    <p className="text-[11px] leading-relaxed">
                      {m.text || 'The assistant is switched off or busy; the dashboard still works'}
                    </p>
                  </div>
                ) : m.errorType === 'network' ? (
                  // Friendly error card for Network Error
                  <div
                    data-testid="error-card-network"
                    className="w-full rounded-xl border border-rose-200 bg-rose-50/80 p-3.5 text-xs text-rose-900 space-y-1.5 shadow-xs"
                  >
                    <div className="flex items-center gap-2 font-semibold text-rose-800">
                      <WifiOff className="h-4 w-4 text-rose-600 shrink-0" />
                      <span>Network Error</span>
                    </div>
                    <p className="text-[11px] leading-relaxed">
                      Network error: unable to reach the AI assistant. Please check your connection.
                    </p>
                  </div>
                ) : (
                  // Normal Assistant message card
                  <div className="w-full rounded-xl bg-slate-50/60 border border-slate-200/90 p-4 text-xs shadow-xs text-slate-800 space-y-3">
                    <StudioMarkdownRenderer content={m.text} />
                    <div className="pt-2 border-t border-slate-200/60 flex items-center justify-between">
                      <button
                        type="button"
                        onClick={() => handleCopyAnswer(m.text, m.id)}
                        className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 hover:border-slate-300 shadow-2xs transition cursor-pointer"
                        aria-label="Copy answer"
                      >
                        {copiedId === m.id ? (
                          <>
                            <Check className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                            <span className="text-emerald-700 font-semibold">Copied!</span>
                          </>
                        ) : (
                          <>
                            <Copy className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                            <span>Copy answer</span>
                          </>
                        )}
                      </button>

                      {isStreaming && (
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500 font-medium">
                            <Loader2 className="h-3 w-3 animate-spin text-slate-400" />
                            Checking the ledger
                          </span>
                          <button
                            type="button"
                            onClick={handleStop}
                            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-slate-100 transition cursor-pointer"
                          >
                            <StopCircle className="h-3 w-3 text-rose-500 shrink-0" />
                            <span>Stop</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 3. Bottom docked section: Prompt Starters + Input Form */}
      <div className="pt-2 border-t border-slate-100 shrink-0 space-y-2">
        {/* Three prompt starters as chips */}
        <div className="flex flex-wrap gap-1.5">
          {PROMPT_STARTERS.map((starter) => (
            <button
              key={starter.label}
              type="button"
              disabled={isStreaming || (countdown !== null && countdown > 0)}
              onClick={() => handleSend(starter.prompt)}
              className="text-left text-[11px] rounded-full border border-slate-200 bg-slate-50 hover:bg-slate-100 hover:border-slate-300 px-3 py-1 text-slate-700 font-medium transition disabled:opacity-40 cursor-pointer inline-flex items-center gap-1.5 max-w-full"
            >
              <Sparkles className="h-3 w-3 text-amber-500 shrink-0" />
              <span className="truncate">{starter.label}</span>
            </button>
          ))}
        </div>

        {/* Input Form with Enter to send */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend(prompt);
          }}
          className="flex items-center gap-2"
        >
          <input
            type="text"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend(prompt);
              }
            }}
            placeholder={
              countdown !== null && countdown > 0
                ? `Rate limit active (${countdown}s)...`
                : 'Ask about recoveries or risk...'
            }
            disabled={isStreaming}
            className="flex-1 min-w-0 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 placeholder-slate-400 focus:border-slate-900 focus:ring-1 focus:ring-slate-900 focus:outline-hidden disabled:bg-slate-50 disabled:text-slate-400"
          />
          <button
            type="submit"
            disabled={!prompt.trim() || isStreaming}
            className="rounded-lg bg-slate-900 px-3 py-2 text-white hover:bg-slate-800 disabled:opacity-40 transition cursor-pointer flex items-center justify-center shrink-0"
            aria-label="Send prompt"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </form>
      </div>
    </div>
  );
};