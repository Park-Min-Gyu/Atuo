import type { Price } from './config.js';
import type { ProviderName, Tier, Usage } from './types.js';
import { DebouncedSaver, loadJson, localDay } from './util.js';

export interface ModelStats {
  provider: ProviderName;
  requests: number;
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  costUsd: number;
}

export interface DayStats {
  models: Record<string, ModelStats>;
  tiers: Record<Tier, number>;
  fallbacks: number;
  cacheHits: number;
  cacheSavedTokens: number;
  cacheSavedUsd: number;
  compressionSavedTokens: number;
  summarySavedTokens: number;
  /** What the day's traffic would have cost if every request had gone to the premium model. */
  premiumOnlyUsd: number;
}

function emptyDay(): DayStats {
  return {
    models: {},
    tiers: { cheap: 0, mid: 0, smart: 0 },
    fallbacks: 0,
    cacheHits: 0,
    cacheSavedTokens: 0,
    cacheSavedUsd: 0,
    compressionSavedTokens: 0,
    summarySavedTokens: 0,
    premiumOnlyUsd: 0,
  };
}

const KEEP_DAYS = 60;

export class UsageStore {
  private readonly days: Record<string, DayStats>;
  private readonly saver: DebouncedSaver;

  constructor(
    file: string | null,
    private readonly pricing: Record<string, Price>,
    /** The model savings are measured against (the configured Claude model). */
    private readonly premiumModel: string,
  ) {
    this.days = file ? loadJson<Record<string, DayStats>>(file, {}) : {};
    this.saver = new DebouncedSaver(file, () => this.days, 2000);
  }

  private today(): DayStats {
    const key = localDay();
    let day = this.days[key];
    if (!day) {
      day = this.days[key] = emptyDay();
      for (const old of Object.keys(this.days).sort().slice(0, -KEEP_DAYS)) delete this.days[old];
    }
    return day;
  }

  /** Longest-prefix match, so dated or suffixed ids still find their family's price. */
  priceFor(model: string): Price | undefined {
    let best: string | undefined;
    for (const id of Object.keys(this.pricing)) {
      if (model.startsWith(id) && (!best || id.length > best.length)) best = id;
    }
    return best ? this.pricing[best] : undefined;
  }

  cost(model: string, usage: Usage): number {
    const price = this.priceFor(model);
    if (!price) return 0;
    const cacheRead = usage.prompt_tokens_details?.cached_tokens ?? 0;
    const cacheWrite = usage.prompt_tokens_details?.cache_write_tokens ?? 0;
    const plainInput = Math.max(0, usage.prompt_tokens - cacheRead - cacheWrite);
    return (
      (plainInput * price.in +
        cacheRead * (price.cacheRead ?? price.in * 0.1) +
        cacheWrite * (price.cacheWrite ?? price.in * 1.25) +
        usage.completion_tokens * price.out) /
      1_000_000
    );
  }

  private premiumCost(usage: Usage): number {
    const price = this.priceFor(this.premiumModel);
    if (!price) return 0;
    return (usage.prompt_tokens * price.in + usage.completion_tokens * price.out) / 1_000_000;
  }

  record(provider: ProviderName, model: string, usage: Usage | undefined, tier: Tier | null): void {
    const day = this.today();
    const stats = (day.models[model] ??= {
      provider,
      requests: 0,
      inputTokens: 0,
      cachedInputTokens: 0,
      outputTokens: 0,
      costUsd: 0,
    });
    stats.requests++;
    if (tier) day.tiers[tier]++;
    if (usage) {
      stats.inputTokens += usage.prompt_tokens;
      stats.cachedInputTokens += usage.prompt_tokens_details?.cached_tokens ?? 0;
      stats.outputTokens += usage.completion_tokens;
      stats.costUsd += this.cost(model, usage);
      day.premiumOnlyUsd += this.premiumCost(usage);
    }
    this.saver.schedule();
  }

  recordCacheHit(model: string, usage: Usage | undefined): void {
    const day = this.today();
    day.cacheHits++;
    if (usage) {
      day.cacheSavedTokens += usage.total_tokens;
      day.cacheSavedUsd += this.cost(model, usage);
      day.premiumOnlyUsd += this.premiumCost(usage);
    }
    this.saver.schedule();
  }

  recordFallback(): void {
    this.today().fallbacks++;
    this.saver.schedule();
  }

  recordCompression(tokensSaved: number): void {
    if (tokensSaved <= 0) return;
    this.today().compressionSavedTokens += tokensSaved;
    this.saver.schedule();
  }

  recordSummary(tokensSaved: number): void {
    if (tokensSaved <= 0) return;
    this.today().summarySavedTokens += tokensSaved;
    this.saver.schedule();
  }

  /** Input + output tokens sent to a provider today (drives the Claude daily budget). */
  tokensToday(provider: ProviderName): number {
    let total = 0;
    for (const stats of Object.values(this.today().models)) {
      if (stats.provider === provider) total += stats.inputTokens + stats.outputTokens;
    }
    return total;
  }

  snapshot(): { today: string; days: Record<string, DayStats> } {
    this.today();
    return { today: localDay(), days: this.days };
  }

  flush(): void {
    this.saver.flush();
  }
}
