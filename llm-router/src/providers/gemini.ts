import type { Config } from '../config.js';
import type { ToolOrigins } from '../origins.js';
import {
  UpstreamError,
  type CallOptions,
  type ChatChunk,
  type ChatCompletion,
  type ChatMessage,
  type ChatRequest,
  type Provider,
  type Usage,
} from '../types.js';
import { newId } from '../util.js';

// Request fields Gemini's OpenAI-compatible endpoint is known to accept;
// anything else a client sends is dropped rather than risking a 400.
const PASS_THROUGH = [
  'tools',
  'tool_choice',
  'temperature',
  'top_p',
  'stop',
  'response_format',
  'reasoning_effort',
  'n',
  'seed',
] as const;

interface RawUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  prompt_tokens_details?: { cached_tokens?: number };
}

function normalizeUsage(raw: RawUsage | null | undefined): Usage | undefined {
  if (!raw) return undefined;
  const prompt = raw.prompt_tokens ?? 0;
  // Thinking tokens are billed as output but may only show up in the total.
  const completion = Math.max(raw.completion_tokens ?? 0, (raw.total_tokens ?? 0) - prompt);
  const cached = raw.prompt_tokens_details?.cached_tokens;
  return {
    prompt_tokens: prompt,
    completion_tokens: completion,
    total_tokens: prompt + completion,
    ...(cached ? { prompt_tokens_details: { cached_tokens: cached } } : {}),
  };
}

function errorMessage(body: string): string {
  try {
    const parsed = JSON.parse(body) as unknown;
    const first = Array.isArray(parsed) ? parsed[0] : parsed;
    const message = (first as { error?: { message?: string } })?.error?.message;
    if (message) return message;
  } catch {
    // fall through to the raw body
  }
  return body.slice(0, 500) || 'empty error response';
}

function retryAfterMs(res: Response): number | undefined {
  const seconds = Number(res.headers.get('retry-after'));
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : undefined;
}

/** Gemini through Google's OpenAI-compatible endpoint: near pass-through. */
export class GeminiProvider implements Provider {
  readonly name = 'gemini' as const;

  constructor(
    private readonly cfg: Config['gemini'],
    private readonly origins: ToolOrigins,
    private readonly maxOutputTokens: number | null,
  ) {}

  private messages(messages: ChatMessage[]): ChatMessage[] {
    return messages.map((message) => {
      let next = message.role === 'developer' ? { ...message, role: 'system' as const } : message;
      if (next.tool_calls?.some((call) => call.extra_content === undefined)) {
        next = {
          ...next,
          tool_calls: next.tool_calls.map((call) => {
            const extra = call.extra_content ?? this.origins.get(call.id)?.extra;
            return extra === undefined ? call : { ...call, extra_content: extra };
          }),
        };
      }
      return next;
    });
  }

  private body(req: ChatRequest, model: string, stream: boolean): Record<string, unknown> {
    const body: Record<string, unknown> = { model, messages: this.messages(req.messages) };
    for (const key of PASS_THROUGH) {
      if (req[key] !== undefined && req[key] !== null) body[key] = req[key];
    }
    const requested = req.max_tokens ?? req.max_completion_tokens;
    const max = this.maxOutputTokens ? Math.min(requested ?? Infinity, this.maxOutputTokens) : requested;
    if (max !== undefined && Number.isFinite(max)) body.max_tokens = max;
    if (stream) {
      body.stream = true;
      body.stream_options = { include_usage: true };
    }
    return body;
  }

  private async post(req: ChatRequest, model: string, stream: boolean, opts: CallOptions): Promise<Response> {
    if (!this.cfg.apiKey) throw new UpstreamError('gemini', 401, 'GEMINI_API_KEY is not set');
    // The timeout covers connection + response headers; a streaming body may run longer.
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), this.cfg.timeoutMs);
    const signal = opts.signal ? AbortSignal.any([opts.signal, timeout.signal]) : timeout.signal;
    let res: Response;
    try {
      res = await fetch(`${this.cfg.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${this.cfg.apiKey}` },
        body: JSON.stringify(this.body(req, model, stream)),
        signal,
      });
    } catch (err) {
      if (opts.signal?.aborted) throw err;
      if (timeout.signal.aborted) throw new UpstreamError('gemini', 504, `Gemini did not respond within ${this.cfg.timeoutMs} ms`);
      throw new UpstreamError('gemini', 503, `Could not reach Gemini: ${(err as Error).message}`);
    } finally {
      if (!stream) clearTimeout(timer);
    }
    if (stream) clearTimeout(timer);
    if (!res.ok) {
      throw new UpstreamError('gemini', res.status, errorMessage(await res.text()), retryAfterMs(res));
    }
    return res;
  }

  async complete(req: ChatRequest, model: string, opts: CallOptions): Promise<ChatCompletion> {
    const res = await this.post(req, model, false, opts);
    const raw = (await res.json()) as ChatCompletion & { usage?: RawUsage };
    if (!Array.isArray(raw.choices) || !raw.choices.length) {
      throw new UpstreamError('gemini', 502, 'Gemini returned no choices');
    }
    return {
      id: raw.id || newId('chatcmpl'),
      object: 'chat.completion',
      created: raw.created || Math.floor(Date.now() / 1000),
      model: raw.model || model,
      choices: raw.choices,
      usage: normalizeUsage(raw.usage),
    };
  }

  async *stream(req: ChatRequest, model: string, opts: CallOptions): AsyncGenerator<ChatChunk> {
    const res = await this.post(req, model, true, opts);
    if (!res.body) throw new UpstreamError('gemini', 502, 'Gemini returned an empty stream');

    const id = newId('chatcmpl');
    const created = Math.floor(Date.now() / 1000);
    const decoder = new TextDecoder();
    let buffer = '';
    let usage: Usage | undefined;

    for await (const bytes of res.body) {
      buffer += decoder.decode(bytes, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (!data || data === '[DONE]') continue;
        let raw: Partial<ChatChunk> & { usage?: RawUsage | null; error?: { message?: string } };
        try {
          raw = JSON.parse(data);
        } catch {
          continue;
        }
        if (raw.error) throw new UpstreamError('gemini', 502, raw.error.message ?? 'Gemini stream error');
        usage = normalizeUsage(raw.usage) ?? usage;
        if (!raw.choices?.length) continue;
        yield { id, object: 'chat.completion.chunk', created, model: raw.model || model, choices: raw.choices };
      }
    }
    // Usage always travels as one trailing chunk; the server decides whether the client sees it.
    yield { id, object: 'chat.completion.chunk', created, model, choices: [], usage: usage ?? null };
  }
}
