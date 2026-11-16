import path from 'node:path';
import { ResponseCache } from './cache.js';
import { compressMessages } from './compress.js';
import type { CompressionMode, Config } from './config.js';
import { HistoryCompactor } from './history.js';
import { ToolOrigins } from './origins.js';
import { ClaudeProvider } from './providers/claude.js';
import { GeminiProvider } from './providers/gemini.js';
import { planRoute, type Candidate, type Plan } from './router.js';
import {
  UpstreamError,
  type ChatChunk,
  type ChatCompletion,
  type ChatMessage,
  type ChatRequest,
  type FinishReason,
  type Provider,
  type ProviderName,
  type Tier,
  type ToolCall,
  type Usage,
} from './types.js';
import { UsageStore } from './usage.js';
import { newId } from './util.js';

export interface ChatOptions {
  signal?: AbortSignal;
  /** false skips the response cache for this request. */
  cache?: boolean;
  compression?: CompressionMode;
  tier?: Tier;
}

export interface ChatMeta {
  provider: ProviderName | 'cache';
  model: string;
  tier: Tier | null;
  cache: 'hit' | 'miss' | 'off';
  route: string;
  /** Estimated prompt tokens removed by compression/summarising for this request. */
  savedTokens: number;
}

export type ChatResult =
  | { meta: ChatMeta; completion: ChatCompletion; chunks?: undefined }
  | { meta: ChatMeta; chunks: AsyncGenerator<ChatChunk>; completion?: undefined };

export class RequestError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'RequestError';
  }
}

/** Rebuilds a full completion from stream chunks so streamed answers can be cached and metered. */
class Accumulator {
  private content = '';
  private refusal: string | null = null;
  private finish: FinishReason | null = null;
  private model = '';
  private readonly calls: ToolCall[] = [];
  usage: Usage | undefined;

  push(chunk: ChatChunk): void {
    if (chunk.model) this.model = chunk.model;
    if (chunk.usage) this.usage = chunk.usage;
    const choice = chunk.choices[0];
    if (!choice) return;
    if (choice.delta.content) this.content += choice.delta.content;
    if (choice.delta.refusal) this.refusal = choice.delta.refusal;
    for (const delta of choice.delta.tool_calls ?? []) {
      const call = (this.calls[delta.index] ??= { id: '', type: 'function', function: { name: '', arguments: '' } });
      if (delta.id) call.id = delta.id;
      if (delta.function?.name) call.function.name += delta.function.name;
      if (delta.function?.arguments) call.function.arguments += delta.function.arguments;
      if (delta.extra_content !== undefined) call.extra_content = delta.extra_content;
    }
    if (choice.finish_reason) this.finish = choice.finish_reason;
  }

  get complete(): boolean {
    return this.finish !== null;
  }

  completion(): ChatCompletion {
    const calls = this.calls.filter(Boolean);
    return {
      id: newId('chatcmpl'),
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: this.model,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: this.content || (calls.length ? null : ''),
            ...(calls.length ? { tool_calls: calls } : {}),
            ...(this.refusal ? { refusal: this.refusal } : {}),
          },
          finish_reason: this.finish,
        },
      ],
      usage: this.usage,
    };
  }
}

function validate(req: ChatRequest): void {
  if (!req || typeof req !== 'object' || !Array.isArray(req.messages) || !req.messages.length) {
    throw new RequestError(400, '`messages` must be a non-empty array');
  }
  for (const message of req.messages) {
    if (!message || typeof message.role !== 'string') {
      throw new RequestError(400, 'every message needs a `role`');
    }
  }
}

function cooldownMs(err: unknown): number {
  if (!(err instanceof UpstreamError)) return 10_000;
  if (err.status === 429) return err.retryAfterMs ?? 30_000;
  if (err.status === 401 || err.status === 403) return 300_000;
  if (err.status >= 500) return 10_000;
  return 0; // a 4xx is about this request, not the upstream's health
}

export class RouterApp {
  readonly cache: ResponseCache;
  readonly usage: UsageStore;
  private readonly providers: Partial<Record<ProviderName, Provider>>;
  private readonly origins = new ToolOrigins();
  private readonly cooldowns = new Map<string, number>();
  private readonly compactor: HistoryCompactor | null;
  private readonly startedAt = Date.now();

  /** `providers` is injectable so tests can run without real upstreams. */
  constructor(
    readonly cfg: Config,
    providers?: Partial<Record<ProviderName, Provider>>,
  ) {
    const file = (name: string): string | null => (cfg.dataDir ? path.join(cfg.dataDir, name) : null);
    this.cache = new ResponseCache({ ...cfg.cache, file: file('cache.json') });
    this.usage = new UsageStore(file('usage.json'), cfg.pricing, cfg.claude.model);
    this.providers = providers ?? {
      ...(cfg.gemini.apiKey ? { gemini: new GeminiProvider(cfg.gemini, this.origins, cfg.maxOutputTokens) } : {}),
      ...(cfg.claude.enabled ? { claude: new ClaudeProvider(cfg.claude, cfg.maxOutputTokens) } : {}),
    };

    const gemini = this.providers.gemini;
    this.compactor = gemini
      ? new HistoryCompactor(cfg.compression.historyBudgetTokens, async (prompt) => {
          const model = cfg.gemini.cheapModel;
          const result = await gemini.complete(
            { messages: [{ role: 'user', content: prompt }], reasoning_effort: 'low', max_tokens: 4000 },
            model,
            {},
          );
          this.usage.record('gemini', model, result.usage, null);
          return result.choices[0]?.message.content ?? '';
        })
      : null;
  }

  get hasProviders(): boolean {
    return Boolean(this.providers.gemini || this.providers.claude);
  }

  private claudeOverBudget(): boolean {
    const limit = this.cfg.claude.dailyTokenBudget;
    return limit > 0 && this.usage.tokensToday('claude') >= limit;
  }

  /** The upstream that issued the tool calls the client is now returning results for. */
  private stickyOrigin(messages: ChatMessage[]) {
    if (messages[messages.length - 1]?.role !== 'tool') return undefined;
    for (let i = messages.length - 1; i >= 0; i--) {
      const calls = messages[i]!.tool_calls;
      if (calls?.length) return this.origins.get(calls[0]!.id);
    }
    return undefined;
  }

  private plan(req: ChatRequest, messages: ChatMessage[], tier: Tier | undefined): Plan {
    const plan = planRoute({ ...req, messages }, this.cfg, {
      geminiAvailable: Boolean(this.providers.gemini),
      claudeAvailable: Boolean(this.providers.claude),
      claudeOverBudget: this.claudeOverBudget(),
      forcedTier: tier,
      sticky: this.stickyOrigin(messages),
    });
    if (!plan.candidates.length) {
      throw new RequestError(503, 'No provider is available. Set GEMINI_API_KEY and/or ANTHROPIC_API_KEY.');
    }
    // Upstreams that just failed go to the back of the line instead of being
    // dropped, so a request is still attempted when everything is cooling down.
    const now = Date.now();
    const cooling = (c: Candidate): boolean => (this.cooldowns.get(`${c.provider}:${c.model}`) ?? 0) > now;
    plan.candidates = [...plan.candidates.filter((c) => !cooling(c)), ...plan.candidates.filter(cooling)];
    return plan;
  }

  /** Per-candidate request: easy prompts on the cheap model don't need deep thinking. */
  private requestFor(req: ChatRequest, candidate: Candidate): ChatRequest {
    const { cheapModel, cheapEffort } = this.cfg.gemini;
    if (candidate.provider === 'gemini' && candidate.model === cheapModel && cheapEffort && !req.reasoning_effort) {
      return { ...req, reasoning_effort: cheapEffort };
    }
    return req;
  }

  private failed(candidate: Candidate, err: unknown, hasNext: boolean): void {
    const ms = cooldownMs(err);
    if (ms > 0) this.cooldowns.set(`${candidate.provider}:${candidate.model}`, Date.now() + ms);
    if (hasNext) this.usage.recordFallback();
    console.warn(
      `[atuo] ${candidate.provider}:${candidate.model} failed (${err instanceof UpstreamError ? err.status : 'error'}): ${(err as Error).message}${hasNext ? ' — trying next' : ''}`,
    );
  }

  private succeeded(
    candidate: Candidate,
    completion: ChatCompletion,
    tier: Tier | null,
    cacheKey: string | null,
  ): void {
    const choice = completion.choices[0];
    this.usage.record(candidate.provider, candidate.model, completion.usage, tier);
    this.origins.remember(choice?.message.tool_calls, candidate.provider, candidate.model);
    // Only complete answers are worth replaying: not truncated or refused ones.
    const finished = choice?.finish_reason === 'stop' || choice?.finish_reason === 'tool_calls';
    const hasOutput = Boolean(choice?.message.content || choice?.message.tool_calls?.length);
    if (cacheKey && finished && hasOutput) {
      this.cache.set(cacheKey, { completion, provider: candidate.provider, at: Date.now() });
    }
  }

  async chat(req: ChatRequest, opts: ChatOptions = {}): Promise<ChatResult> {
    validate(req);
    const mode = opts.compression ?? this.cfg.compression.mode;
    const compressed = compressMessages(req.messages, mode);
    let messages = compressed.messages;
    let savedTokens = compressed.tokensSaved;

    const useCache = this.cache.enabled && opts.cache !== false;
    const cacheKey = useCache ? ResponseCache.key(req, messages) : null;
    const hit = cacheKey ? this.cache.get(cacheKey) : undefined;
    if (hit) {
      this.usage.recordCacheHit(hit.completion.model, hit.completion.usage);
      const meta: ChatMeta = {
        provider: 'cache',
        model: hit.completion.model,
        tier: null,
        cache: 'hit',
        route: `cache hit (originally ${hit.provider})`,
        savedTokens: hit.completion.usage?.total_tokens ?? 0,
      };
      const completion = { ...hit.completion, id: newId('chatcmpl'), created: Math.floor(Date.now() / 1000) };
      return req.stream ? { meta, chunks: replay(completion) } : { meta, completion };
    }

    this.usage.recordCompression(compressed.tokensSaved);
    if (mode === 'aggressive' && this.compactor) {
      const history = await this.compactor.apply(messages);
      messages = history.messages;
      savedTokens += history.tokensSaved;
      this.usage.recordSummary(history.tokensSaved);
    }

    const plan = this.plan(req, messages, opts.tier);
    const upstreamReq: ChatRequest = { ...req, messages };
    const meta = (candidate: Candidate): ChatMeta => ({
      provider: candidate.provider,
      model: candidate.model,
      tier: plan.tier,
      cache: useCache ? 'miss' : 'off',
      route: plan.reason,
      savedTokens,
    });

    let firstError: unknown;
    for (const [index, candidate] of plan.candidates.entries()) {
      const provider = this.providers[candidate.provider]!;
      const request = this.requestFor(upstreamReq, candidate);
      const hasNext = index < plan.candidates.length - 1;
      try {
        if (!req.stream) {
          const completion = await provider.complete(request, candidate.model, { signal: opts.signal });
          this.succeeded(candidate, completion, plan.tier, cacheKey);
          return { meta: meta(candidate), completion };
        }
        // Pull the first chunk before committing: an upstream that rejects the
        // request throws here, while falling over is still possible.
        const upstream = provider.stream(request, candidate.model, { signal: opts.signal });
        const first = await upstream.next();
        return { meta: meta(candidate), chunks: this.relay(first, upstream, candidate, plan.tier, cacheKey) };
      } catch (err) {
        if (opts.signal?.aborted) throw err;
        firstError ??= err;
        this.failed(candidate, err, hasNext);
      }
    }
    throw firstError;
  }

  private async *relay(
    first: IteratorResult<ChatChunk>,
    upstream: AsyncGenerator<ChatChunk>,
    candidate: Candidate,
    tier: Tier | null,
    cacheKey: string | null,
  ): AsyncGenerator<ChatChunk> {
    const acc = new Accumulator();
    try {
      for (let next = first; !next.done; next = await upstream.next()) {
        acc.push(next.value);
        yield next.value;
      }
      if (acc.complete) this.succeeded(candidate, acc.completion(), tier, cacheKey);
    } finally {
      // Closes the upstream connection if the client went away mid-stream.
      await upstream.return(undefined).catch(() => {});
    }
  }

  models(): Array<{ id: string; object: 'model'; owned_by: string }> {
    const ids = ['auto', 'cheap', 'mid', 'smart'];
    if (this.providers.gemini) ids.push(this.cfg.gemini.cheapModel, this.cfg.gemini.midModel);
    if (this.providers.claude) ids.push(this.cfg.claude.model);
    return [...new Set(ids)].map((id) => ({ id, object: 'model', owned_by: 'atuo-router' }));
  }

  stats() {
    const now = Date.now();
    return {
      uptimeSeconds: Math.round((now - this.startedAt) / 1000),
      providers: {
        gemini: {
          enabled: Boolean(this.providers.gemini),
          cheapModel: this.cfg.gemini.cheapModel,
          midModel: this.cfg.gemini.midModel,
        },
        claude: {
          enabled: Boolean(this.providers.claude),
          model: this.cfg.claude.model,
          dailyTokenBudget: this.cfg.claude.dailyTokenBudget,
          tokensToday: this.usage.tokensToday('claude'),
        },
      },
      compression: this.cfg.compression.mode,
      cache: {
        enabled: this.cache.enabled,
        entries: this.cache.size,
        hits: this.cache.hits,
        misses: this.cache.misses,
      },
      cooldowns: [...this.cooldowns]
        .filter(([, until]) => until > now)
        .map(([upstream, until]) => ({ upstream, secondsLeft: Math.ceil((until - now) / 1000) })),
      usage: this.usage.snapshot(),
    };
  }

  close(): void {
    this.cache.flush();
    this.usage.flush();
  }
}

/** A cached completion, replayed in stream form. */
async function* replay(completion: ChatCompletion): AsyncGenerator<ChatChunk> {
  const choice = completion.choices[0]!;
  const base = {
    id: completion.id,
    object: 'chat.completion.chunk' as const,
    created: completion.created,
    model: completion.model,
  };
  yield {
    ...base,
    choices: [
      {
        index: 0,
        delta: {
          role: 'assistant',
          content: choice.message.content ?? '',
          ...(choice.message.tool_calls
            ? { tool_calls: choice.message.tool_calls.map((call, index) => ({ ...call, index })) }
            : {}),
        },
        finish_reason: null,
      },
    ],
  };
  yield { ...base, choices: [{ index: 0, delta: {}, finish_reason: choice.finish_reason }] };
  yield { ...base, choices: [], usage: completion.usage ?? null };
}
