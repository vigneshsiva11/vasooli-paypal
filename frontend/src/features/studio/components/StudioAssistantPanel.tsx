import React, { useState } from 'react';
import { Bot, Send, Sparkles, AlertCircle, X, ChevronRight, MessageSquare } from 'lucide-react';
import { PROMPT_STARTERS, StudioLlmAdapter } from '../api/studioAi';
import { isStudioAiEnabled } from '../utils/studioAiCheck';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

export const StudioAssistantPanel: React.FC = () => {
  // If the flag is off, render absolutely nothing assistant-related
  if (!isStudioAiEnabled()) {
    return null;
  }

  const [isOpen, setIsOpen] = useState(true);
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSend = async (userPromptText: string) => {
    const textToSend = userPromptText.trim();
    if (!textToSend || isStreaming) return;

    setErrorMsg(null);
    const userMsgId = `user_${Date.now()}`;
    const botMsgId = `asst_${Date.now()}`;

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', text: textToSend },
      { id: botMsgId, role: 'assistant', text: '' },
    ]);
    setPrompt('');
    setIsStreaming(true);

    try {
      const adapter = new StudioLlmAdapter();
      const handler = adapter.executeTurn({
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
      });

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
    } catch (err: any) {
      setErrorMsg(err instanceof Error ? err.message : 'Unable to complete assistant turn.');
    } finally {
      setIsStreaming(false);
    }
  };

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="fixed bottom-6 right-6 z-40 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-[var(--color-primary)] px-4 py-2.5 text-xs font-semibold text-white shadow-lg hover:opacity-90 transition cursor-pointer"
        aria-label="Open Studio Assistant"
      >
        <Sparkles className="h-4 w-4 text-amber-300" />
        <span>Studio Assistant</span>
      </button>
    );
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col h-full min-h-[460px] w-full xl:w-88 shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--color-accent)]/10 text-[var(--color-accent)]">
            <Bot className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-primary)]">
              Studio Assistant
            </h3>
            <span className="text-[10px] font-medium text-emerald-600 bg-emerald-50 px-1.5 py-0.2 rounded border border-emerald-200">
              Active
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="rounded p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition"
          aria-label="Collapse assistant panel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Short disclaimer */}
      <div className="rounded-md bg-amber-50/70 border border-amber-200/60 p-2.5 mb-3 text-[11px] text-amber-900 leading-snug">
        <p className="font-medium">Read-only analysis of the sanitized ledger</p>
      </div>

      {/* Prompt Starter Chips */}
      <div className="mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] block mb-1.5">
          Prompt Starters
        </span>
        <div className="flex flex-col gap-1.5">
          {PROMPT_STARTERS.map((starter) => (
            <button
              key={starter.label}
              type="button"
              disabled={isStreaming}
              onClick={() => handleSend(starter.prompt)}
              className="text-left text-[11px] rounded-lg border border-gray-200 bg-[var(--color-bg-surface)] hover:bg-gray-100 hover:border-gray-300 px-2.5 py-1.5 text-[var(--color-primary)] transition disabled:opacity-50 cursor-pointer flex items-center justify-between group"
            >
              <span className="truncate">{starter.label}</span>
              <ChevronRight className="h-3 w-3 text-gray-400 group-hover:text-[var(--color-primary)] shrink-0 ml-1" />
            </button>
          ))}
        </div>
      </div>

      {/* Chat / Messages scroll area */}
      <div className="flex-1 min-h-[140px] overflow-y-auto space-y-2.5 pr-1 mb-3 text-xs">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center p-4 text-[var(--color-text-muted)]">
            <MessageSquare className="h-6 w-6 text-gray-300 mb-2" />
            <p className="text-[11px]">
              Ask a question or select a prompt starter above to analyze recoveries.
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={`flex flex-col ${
                m.role === 'user' ? 'items-end' : 'items-start'
              }`}
            >
              <div
                className={`max-w-[90%] rounded-lg p-2.5 text-xs ${
                  m.role === 'user'
                    ? 'bg-[var(--color-primary)] text-white'
                    : 'bg-gray-100 text-[var(--color-primary)] border border-gray-200'
                }`}
              >
                {m.text || (isStreaming ? 'Thinking...' : '')}
              </div>
            </div>
          ))
        )}

        {errorMsg && (
          <div className="flex items-start gap-1.5 rounded-lg border border-red-200 bg-red-50 p-2 text-[11px] text-red-700">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}
      </div>

      {/* Input box */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend(prompt);
        }}
        className="flex items-center gap-1.5 pt-2 border-t border-gray-100"
      >
        <input
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask about recoveries..."
          disabled={isStreaming}
          className="flex-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-[var(--color-primary)] placeholder-gray-400 focus:border-[var(--color-primary)] focus:outline-hidden"
        />
        <button
          type="submit"
          disabled={!prompt.trim() || isStreaming}
          className="rounded-lg bg-[var(--color-primary)] p-2 text-white hover:opacity-90 disabled:opacity-40 transition cursor-pointer"
          aria-label="Send prompt"
        >
          <Send className="h-3.5 w-3.5" />
        </button>
      </form>
    </div>
  );
};
