import {
  AgStudioAiModule,
  AgStudioModuleRegistry,
  createAiHarness,
  type AgAiEvent,
  type AgAiHarnessSetup,
  type AgAiOutputItem,
  type AgAiPromptStarter,
  type AgLlmAdapter,
  type AgLlmExecuteTurnOptions,
  type AgLlmRequest,
  type AgLlmResponse,
  type AgLlmResponseHandler,
} from 'ag-studio';
import { streamApiClient } from '@/api/client';

export const PROMPT_STARTERS: AgAiPromptStarter[] = [
  {
    label: 'Which root cause has the most revenue at risk?',
    prompt: 'Which root cause has the most revenue at risk?',
  },
  {
    label: 'Show verified vs asserted recoveries',
    prompt: 'Show verified vs asserted recoveries',
  },
  {
    label: 'Add a chart of recoveries by status',
    prompt: 'Add a chart of recoveries by status',
  },
];

export function mapAiError(
  status: number | null | undefined,
  retryAfter?: number | string | null,
  rawError?: unknown
): string {
  if (status === 429) {
    const seconds = Number(retryAfter) || 60;
    return `Rate limit reached. Please retry in ${seconds}s.`;
  }
  if (status === 503) {
    return 'The assistant is switched off or busy; the dashboard still works';
  }
  if (rawError instanceof Error && rawError.name === 'AbortError') {
    return 'Request cancelled.';
  }
  return 'Network error: unable to reach the AI assistant. Please check your connection.';
}

async function* parseSseStream(response: Response): AsyncGenerator<AgAiEvent> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(':')) continue;
        if (trimmed === 'data: [DONE]') return;
        if (trimmed.startsWith('data: ')) {
          const jsonStr = trimmed.slice(6);
          try {
            const event = JSON.parse(jsonStr) as AgAiEvent;
            yield event;
          } catch (_) {
            // ignore non-json lines
          }
        }
      }
    }

    if (buffer.trim().startsWith('data: ') && buffer.trim() !== 'data: [DONE]') {
      try {
        const event = JSON.parse(buffer.trim().slice(6)) as AgAiEvent;
        yield event;
      } catch (_) {}
    }
  } finally {
    reader.releaseLock();
  }
}

export class StudioLlmAdapter implements AgLlmAdapter {
  executeTurn(request: AgLlmRequest, options?: AgLlmExecuteTurnOptions): AgLlmResponseHandler {
    const responseId = `resp_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
    const startTime = Date.now();
    let resolveComplete: (resp: AgLlmResponse) => void;

    const completePromise = new Promise<AgLlmResponse>((resolve) => {
      resolveComplete = resolve;
    });

    const stream = {
      async *[Symbol.asyncIterator](): AsyncIterator<AgAiEvent> {
        const outputItems: AgAiOutputItem[] = [];
        let currentText = '';
        let currentMessageId = '';
        let currentToolCallId = '';
        let currentToolName = '';
        let currentToolArgs = '';

        try {
          const response = await streamApiClient('/ai/studio', request, { signal: options?.signal });

          if (!response.ok) {
            let retryAfter: string | null = null;
            if (response.status === 429) {
              retryAfter = response.headers.get('Retry-After');
            }

            const friendlyMessage = mapAiError(response.status, retryAfter);
            const errId = `err_${Date.now()}`;

            yield {
              type: 'TEXT_MESSAGE_START',
              messageId: errId,
              role: 'assistant',
            };
            yield {
              type: 'TEXT_MESSAGE_CONTENT',
              messageId: errId,
              delta: friendlyMessage,
            };
            yield {
              type: 'TEXT_MESSAGE_END',
              messageId: errId,
            };

            resolveComplete({
              id: responseId,
              createdAt: startTime,
              status: 'completed',
              output: [
                {
                  id: errId,
                  kind: 'output',
                  type: 'message',
                  role: 'assistant',
                  status: 'completed',
                  content: [{ type: 'text', text: friendlyMessage, annotations: [] }],
                },
              ],
            });
            return;
          }

          for await (const event of parseSseStream(response)) {
            yield event;

            if (event.type === 'TEXT_MESSAGE_START') {
              currentMessageId = event.messageId;
              currentText = '';
            } else if (event.type === 'TEXT_MESSAGE_CONTENT') {
              currentText += event.delta;
            } else if (event.type === 'TEXT_MESSAGE_END') {
              outputItems.push({
                id: currentMessageId || event.messageId,
                kind: 'output',
                type: 'message',
                role: 'assistant',
                status: 'completed',
                content: [{ type: 'text', text: currentText, annotations: [] }],
              });
              currentText = '';
            } else if (event.type === 'TOOL_CALL_START') {
              currentToolCallId = event.toolCallId;
              currentToolName = event.toolCallName;
              currentToolArgs = '';
            } else if (event.type === 'TOOL_CALL_ARGS') {
              currentToolArgs += event.delta;
            } else if (event.type === 'TOOL_CALL_END') {
              outputItems.push({
                id: currentToolCallId || event.toolCallId,
                callId: currentToolCallId || event.toolCallId,
                kind: 'output',
                type: 'function_call',
                name: currentToolName,
                arguments: currentToolArgs,
              });
              currentToolArgs = '';
            }
          }

          resolveComplete({
            id: responseId,
            createdAt: startTime,
            status: 'completed',
            output: outputItems,
          });
        } catch (err: any) {
          const friendlyMessage = mapAiError(null, null, err);
          const errId = `err_${Date.now()}`;

          yield {
            type: 'TEXT_MESSAGE_START',
            messageId: errId,
            role: 'assistant',
          };
          yield {
            type: 'TEXT_MESSAGE_CONTENT',
            messageId: errId,
            delta: friendlyMessage,
          };
          yield {
            type: 'TEXT_MESSAGE_END',
            messageId: errId,
          };

          resolveComplete({
            id: responseId,
            createdAt: startTime,
            status: 'completed',
            output: [
              {
                id: errId,
                kind: 'output',
                type: 'message',
                role: 'assistant',
                status: 'completed',
                content: [{ type: 'text', text: friendlyMessage, annotations: [] }],
              },
            ],
          });
        }
      },
    };

    return {
      stream,
      complete: completePromise,
    };
  }
}

let aiRegistered = false;

export const registerAiModule = (): void => {
  if (!aiRegistered) {
    AgStudioModuleRegistry.registerModules([AgStudioAiModule]);
    aiRegistered = true;
  }
};

export const getStudioAiSetup = () => {
  registerAiModule();
  const adapter = new StudioLlmAdapter();

  const aiHarnessSetup: AgAiHarnessSetup = (params) => {
    return createAiHarness(params.api, {
      adapter,
      promptStarters: PROMPT_STARTERS,
    });
  };

  return {
    modules: [AgStudioAiModule],
    ai: aiHarnessSetup,
    adapter,
  };
};
