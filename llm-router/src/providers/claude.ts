import Anthropic from '@anthropic-ai/sdk';
import type { Config } from '../config.js';
import {
  UpstreamError,
  type CallOptions,
  type ChatChunk,
  type ChatCompletion,
  type ChatMessage,
  type ChatRequest,
  type FinishReason,
  type Provider,
  type ToolCall,
  type Usage,
} from '../types.js';
import { newId } from '../util.js';

type Params = Anthropic.Beta.Messages.MessageCreateParamsNonStreaming;
type Block = Anthropic.Beta.Messages.BetaContentBlockParam;
type Message = Anthropic.Beta.Messages.BetaMessage;
type Effort = NonNullable<Anthropic.Beta.Messages.BetaOutputConfig['effort']>;

// Models that accept the server-side refusal fallback (`fallbacks: "default"`).
const REFUSAL_FALLBACK_MODELS = [
  'claude-fable-5-2',
  'claude-fable-5-1',
  'claude-opus-5-5',
  'claude-opus-5',
  'claude-sonnet-5-5',
];
const REFUSAL_FALLBACK_BETA = 'server-side-fallback-2026-07-01';

const DEFAULT_MAX_TOKENS = 16_000;
const DEFAULT_MAX_TOKENS_STREAMING = 64_000;
const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'] as const;
type ImageType = (typeof IMAGE_TYPES)[number];

const EFFORTS: Record<string, Effort> = {
  none: 'low',
  minimal: 'low',
  low: 'low',
  medium: 'medium',
  high: 'high',
  xhigh: 'xhigh',
  max: 'max',
};

/** Anthropic tool ids must match ^[a-zA-Z0-9_-]+$; ids minted by other providers may not. */
function toolId(id: string): string {
  return id.replace(/[^a-zA-Z0-9_-]/g, '_') || 'tool_call';
}

function userBlocks(content: ChatMessage['content']): Block[] {
  if (typeof content === 'string') return content ? [{ type: 'text', text: content }] : [];
  if (!Array.isArray(content)) return [];
  const blocks: Block[] = [];
  for (const part of content) {
    if (part.type === 'text' && part.text) {
      blocks.push({ type: 'text', text: part.text });
    } else if (part.type === 'image_url' && part.image_url?.url) {
      const url = part.image_url.url;
      const data = /^data:([^;,]+);base64,(.+)$/s.exec(url);
      if (!data) {
        blocks.push({ type: 'image', source: { type: 'url', url } });
      } else if ((IMAGE_TYPES as readonly string[]).includes(data[1]!)) {
        blocks.push({
          type: 'image',
          source: { type: 'base64', media_type: data[1] as ImageType, data: data[2]! },
        });
      }
    }
  }
  return blocks;
}

function textOf(content: ChatMessage['content']): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.map((p) => (p.type === 'text' ? (p.text ?? '') : '')).join('');
}

function parseArguments(raw: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(raw || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/** OpenAI chat messages -> Anthropic `system` + alternating `messages`. */
export function toAnthropicMessages(messages: ChatMessage[]): {
  system: string;
  messages: Anthropic.Beta.Messages.BetaMessageParam[];
} {
  const system: string[] = [];
  const out: Array<{ role: 'user' | 'assistant'; content: Block[] }> = [];
  const push = (role: 'user' | 'assistant', blocks: Block[]): void => {
    if (!blocks.length) return;
    const last = out[out.length - 1];
    // Merging keeps parallel tool results in one user turn, as the API expects.
    if (last?.role === role) last.content.push(...blocks);
    else out.push({ role, content: blocks });
  };

  for (const message of messages) {
    if (message.role === 'system' || message.role === 'developer') {
      const text = textOf(message.content);
      if (text) system.push(text);
    } else if (message.role === 'user') {
      push('user', userBlocks(message.content));
    } else if (message.role === 'assistant') {
      const blocks: Block[] = [];
      const text = textOf(message.content);
      if (text.trim()) blocks.push({ type: 'text', text });
      for (const call of message.tool_calls ?? []) {
        blocks.push({
          type: 'tool_use',
          id: toolId(call.id),
          name: call.function.name,
          input: parseArguments(call.function.arguments),
        });
      }
      push('assistant', blocks);
    } else if (message.role === 'tool') {
      push('user', [
        {
          type: 'tool_result',
          tool_use_id: toolId(message.tool_call_id ?? ''),
          content: textOf(message.content) || '(empty result)',
        },
      ]);
    }
  }

  if (out[0]?.role !== 'user') {
    out.unshift({ role: 'user', content: [{ type: 'text', text: '(conversation continues)' }] });
  }
  return { system: system.join('\n\n'), messages: out };
}

function finishReason(reason: Message['stop_reason']): FinishReason {
  switch (reason) {
    case 'max_tokens':
    case 'model_context_window_exceeded':
      return 'length';
    case 'tool_use':
      return 'tool_calls';
    case 'refusal':
      return 'content_filter';
    default:
      return 'stop';
  }
}

function toUsage(usage: Message['usage']): Usage {
  const cacheRead = usage.cache_read_input_tokens ?? 0;
  const cacheWrite = usage.cache_creation_input_tokens ?? 0;
  // OpenAI-style prompt_tokens counts cached input too.
  const prompt = usage.input_tokens + cacheRead + cacheWrite;
  return {
    prompt_tokens: prompt,
    completion_tokens: usage.output_tokens,
    total_tokens: prompt + usage.output_tokens,
    ...(cacheRead || cacheWrite
      ? { prompt_tokens_details: { cached_tokens: cacheRead, cache_write_tokens: cacheWrite } }
      : {}),
  };
}

function retryAfterMs(headers: Headers | undefined): number | undefined {
  const seconds = Number(headers?.get('retry-after'));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : undefined;
}

function mapError(err: unknown): never {
  if (err instanceof Anthropic.APIUserAbortError) throw err;
  if (err instanceof Anthropic.RateLimitError) {
    throw new UpstreamError('claude', 429, err.message, retryAfterMs(err.headers));
  }
  if (err instanceof Anthropic.AuthenticationError || err instanceof Anthropic.PermissionDeniedError) {
    throw new UpstreamError('claude', err.status, `Claude rejected the credentials: ${err.message}`);
  }
  if (err instanceof Anthropic.APIConnectionTimeoutError) {
    throw new UpstreamError('claude', 504, 'Claude request timed out');
  }
  if (err instanceof Anthropic.APIConnectionError) {
    throw new UpstreamError('claude', 503, `Could not reach Claude: ${err.message}`);
  }
  if (err instanceof Anthropic.APIError) {
    throw new UpstreamError('claude', err.status ?? 502, err.message, retryAfterMs(err.headers));
  }
  if (err instanceof Anthropic.AnthropicError) {
    // e.g. no credentials could be resolved from the environment
    throw new UpstreamError('claude', 401, err.message);
  }
  throw err;
}

export class ClaudeProvider implements Provider {
  readonly name = 'claude' as const;
  private readonly client: Anthropic;

  constructor(
    private readonly cfg: Config['claude'],
    private readonly maxOutputTokens: number | null,
  ) {
    this.client = new Anthropic({
      maxRetries: 1,
      ...(cfg.apiKey ? { apiKey: cfg.apiKey } : {}),
      ...(cfg.baseUrl ? { baseURL: cfg.baseUrl } : {}),
    });
  }

  /** Exposed for tests: the exact request the router sends for an OpenAI-format request. */
  params(req: ChatRequest, model: string, streaming: boolean): Params {
    const { system, messages } = toAnthropicMessages(req.messages);
    const systemParts = [system];
    if (req.response_format?.type === 'json_object') {
      systemParts.push('Respond with a single valid JSON object and nothing else.');
    }
    const systemText = systemParts.filter(Boolean).join('\n\n');

    const requested = req.max_tokens ?? req.max_completion_tokens;
    let maxTokens = requested ?? (streaming ? DEFAULT_MAX_TOKENS_STREAMING : DEFAULT_MAX_TOKENS);
    if (this.maxOutputTokens) maxTokens = Math.min(maxTokens, this.maxOutputTokens);

    const params: Params = { model, max_tokens: maxTokens, messages };
    if (systemText) params.system = systemText;

    // Sampling parameters (temperature/top_p) are rejected by current Claude
    // models, so they are deliberately not forwarded.
    if (req.stop) params.stop_sequences = Array.isArray(req.stop) ? req.stop : [req.stop];

    if (req.tools?.length) {
      // Tool input is left on the API's default buffered delivery: this proxy
      // cannot validate arguments against the client's schema on its behalf.
      params.tools = req.tools.map((tool) => ({
        name: tool.function.name,
        description: tool.function.description ?? '',
        input_schema: {
          type: 'object' as const,
          ...(tool.function.parameters ?? { properties: {} }),
        },
        ...(tool.function.strict ? { strict: true } : {}),
      }));
      // Forcing a tool ("required" / a named function) is not supported by
      // current models; "auto" is the closest behaviour they accept.
      params.tool_choice =
        req.tool_choice === 'none'
          ? { type: 'none' }
          : { type: 'auto', ...(req.parallel_tool_calls === false ? { disable_parallel_tool_use: true } : {}) };
    }

    const effort = EFFORTS[(req.reasoning_effort ?? this.cfg.effort ?? '').toLowerCase()];
    const schema = req.response_format?.type === 'json_schema' ? req.response_format.json_schema.schema : undefined;
    if (effort || schema) {
      params.output_config = {
        ...(effort ? { effort } : {}),
        ...(schema ? { format: { type: 'json_schema' as const, schema } } : {}),
      };
    }

    // Prompt caching pays off when a prefix is re-sent (multi-turn, system
    // prompt, tools); a lone one-off question would only pay the write surcharge.
    if (this.cfg.promptCache && (systemText || params.tools || messages.length > 1)) {
      params.cache_control = { type: 'ephemeral' };
    }

    if (this.cfg.refusalFallback && REFUSAL_FALLBACK_MODELS.some((id) => model.startsWith(id))) {
      params.betas = [REFUSAL_FALLBACK_BETA];
      params.fallbacks = 'default';
    }
    return params;
  }

  private toCompletion(message: Message): ChatCompletion {
    let text = '';
    const toolCalls: ToolCall[] = [];
    for (const block of message.content) {
      if (block.type === 'text') {
        text += block.text;
      } else if (block.type === 'tool_use') {
        toolCalls.push({
          id: block.id,
          type: 'function',
          function: { name: block.name, arguments: JSON.stringify(block.input ?? {}) },
        });
      }
    }
    const refused = message.stop_reason === 'refusal';
    return {
      id: newId('chatcmpl'),
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: message.model,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: text || (toolCalls.length ? null : ''),
            ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
            ...(refused
              ? { refusal: message.stop_details?.explanation ?? 'The model declined this request.' }
              : {}),
          },
          finish_reason: finishReason(message.stop_reason),
        },
      ],
      usage: toUsage(message.usage),
    };
  }

  async complete(req: ChatRequest, model: string, opts: CallOptions): Promise<ChatCompletion> {
    try {
      const message = await this.client.beta.messages.create(this.params(req, model, false), {
        signal: opts.signal,
      });
      return this.toCompletion(message);
    } catch (err) {
      mapError(err);
    }
  }

  async *stream(req: ChatRequest, model: string, opts: CallOptions): AsyncGenerator<ChatChunk> {
    const id = newId('chatcmpl');
    const created = Math.floor(Date.now() / 1000);
    let servedBy = model;
    const chunk = (delta: ChatChunk['choices'][0]['delta'], finish: FinishReason | null = null): ChatChunk => ({
      id,
      object: 'chat.completion.chunk',
      created,
      model: servedBy,
      choices: [{ index: 0, delta, finish_reason: finish }],
    });

    try {
      const stream = this.client.beta.messages.stream(this.params(req, model, true), { signal: opts.signal });
      // Content-block index -> position in the OpenAI tool_calls array.
      const toolIndex = new Map<number, number>();
      for await (const event of stream) {
        if (event.type === 'message_start') {
          servedBy = event.message.model;
          yield chunk({ role: 'assistant', content: '' });
        } else if (event.type === 'content_block_start' && event.content_block.type === 'tool_use') {
          const index = toolIndex.size;
          toolIndex.set(event.index, index);
          yield chunk({
            tool_calls: [
              {
                index,
                id: event.content_block.id,
                type: 'function',
                function: { name: event.content_block.name, arguments: '' },
              },
            ],
          });
        } else if (event.type === 'content_block_delta') {
          if (event.delta.type === 'text_delta') {
            yield chunk({ content: event.delta.text });
          } else if (event.delta.type === 'input_json_delta') {
            const index = toolIndex.get(event.index);
            if (index !== undefined) {
              yield chunk({ tool_calls: [{ index, function: { arguments: event.delta.partial_json } }] });
            }
          }
        }
      }
      const final = await stream.finalMessage();
      servedBy = final.model;
      const refused = final.stop_reason === 'refusal';
      yield chunk(
        refused ? { refusal: final.stop_details?.explanation ?? 'The model declined this request.' } : {},
        finishReason(final.stop_reason),
      );
      yield { id, object: 'chat.completion.chunk', created, model: servedBy, choices: [], usage: toUsage(final.usage) };
    } catch (err) {
      mapError(err);
    }
  }
}
