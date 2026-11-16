import path from 'node:path';

export type CompressionMode = 'off' | 'lossless' | 'standard' | 'aggressive';
export const COMPRESSION_MODES: readonly CompressionMode[] = ['off', 'lossless', 'standard', 'aggressive'];

/** USD per 1M tokens. `cacheRead`/`cacheWrite` default to 0.1x / 1.25x of `in`. */
export interface Price {
  in: number;
  out: number;
  cacheRead?: number;
  cacheWrite?: number;
}

export interface Config {
  host: string;
  port: number;
  /** When set, every request must send `Authorization: Bearer <this>`. */
  routerApiKey: string | null;
  corsOrigin: string | null;
  /** Directory for cache/usage files; null keeps everything in memory. */
  dataDir: string | null;
  gemini: {
    apiKey: string | null;
    baseUrl: string;
    cheapModel: string;
    midModel: string;
    /** reasoning_effort sent with cheap-tier requests that don't set their own; null = model default. */
    cheapEffort: string | null;
    timeoutMs: number;
  };
  claude: {
    enabled: boolean;
    /** Only for tests/overrides; normally the SDK resolves credentials from the environment. */
    apiKey?: string;
    baseUrl?: string;
    model: string;
    effort: string | null;
    promptCache: boolean;
    refusalFallback: boolean;
    /** Input+output tokens per local day; 0 = unlimited. */
    dailyTokenBudget: number;
  };
  routing: {
    midThreshold: number;
    smartThreshold: number;
    fallback: boolean;
  };
  cache: { enabled: boolean; ttlMs: number; maxEntries: number };
  compression: { mode: CompressionMode; historyBudgetTokens: number };
  /** Upper bound applied to every request's output tokens; null = no cap. */
  maxOutputTokens: number | null;
  pricing: Record<string, Price>;
}

// Estimates only — check the providers' pricing pages and override with PRICING_JSON.
export const DEFAULT_PRICING: Record<string, Price> = {
  'claude-fable-5-2': { in: 8, out: 40, cacheRead: 0.2 },
  'claude-opus-5-5': { in: 4, out: 20, cacheRead: 0.2 },
  'claude-sonnet-5-5': { in: 2, out: 10, cacheRead: 0.1 },
  'claude-haiku-5-5': { in: 0.1, out: 0.5 },
  'gemini-2.5-pro': { in: 1.25, out: 10 },
  'gemini-2.5-flash-lite': { in: 0.1, out: 0.4 },
  'gemini-2.5-flash': { in: 0.3, out: 2.5 },
};

type Env = Record<string, string | undefined>;

function str(env: Env, key: string): string | null {
  const value = env[key]?.trim();
  return value ? value : null;
}

function num(env: Env, key: string, fallback: number): number {
  const raw = str(env, key);
  if (raw === null) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${key} must be a number, got "${raw}"`);
  return value;
}

function bool(env: Env, key: string, fallback: boolean): boolean {
  const raw = str(env, key)?.toLowerCase();
  if (raw == null) return fallback;
  if (['1', 'true', 'yes', 'on'].includes(raw)) return true;
  if (['0', 'false', 'no', 'off'].includes(raw)) return false;
  throw new Error(`${key} must be true or false, got "${raw}"`);
}

export function loadConfig(env: Env = process.env): Config {
  const mode = (str(env, 'COMPRESSION') ?? 'standard').toLowerCase() as CompressionMode;
  if (!COMPRESSION_MODES.includes(mode)) {
    throw new Error(`COMPRESSION must be one of ${COMPRESSION_MODES.join(', ')}, got "${mode}"`);
  }

  let pricing = DEFAULT_PRICING;
  const pricingJson = str(env, 'PRICING_JSON');
  if (pricingJson) {
    try {
      pricing = { ...DEFAULT_PRICING, ...(JSON.parse(pricingJson) as Record<string, Price>) };
    } catch {
      throw new Error('PRICING_JSON is not valid JSON');
    }
  }

  const maxOutput = num(env, 'MAX_OUTPUT_TOKENS', 0);
  const dataDir = str(env, 'DATA_DIR') ?? 'data';

  return {
    host: str(env, 'HOST') ?? '127.0.0.1',
    port: num(env, 'PORT', 8787),
    routerApiKey: str(env, 'ROUTER_API_KEY'),
    corsOrigin: str(env, 'CORS_ORIGIN'),
    dataDir: dataDir === 'none' ? null : path.resolve(dataDir),
    gemini: {
      apiKey: str(env, 'GEMINI_API_KEY') ?? str(env, 'GOOGLE_API_KEY'),
      baseUrl: (str(env, 'GEMINI_BASE_URL') ?? 'https://generativelanguage.googleapis.com/v1beta/openai').replace(/\/+$/, ''),
      cheapModel: str(env, 'GEMINI_CHEAP_MODEL') ?? 'gemini-2.5-flash',
      midModel: str(env, 'GEMINI_MID_MODEL') ?? 'gemini-2.5-pro',
      cheapEffort: ((v) => (v === 'off' ? null : v))((str(env, 'GEMINI_CHEAP_EFFORT') ?? 'low').toLowerCase()),
      timeoutMs: num(env, 'GEMINI_TIMEOUT_MS', 180_000),
    },
    claude: {
      // Settings are prefixed ROUTER_CLAUDE_ because Claude Code itself owns CLAUDE_* variables.
      // The Anthropic SDK reads ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN itself.
      // ROUTER_CLAUDE_ENABLED=true covers `ant auth login` profiles with no env var set.
      enabled:
        bool(env, 'ROUTER_CLAUDE_ENABLED', false) ||
        str(env, 'ANTHROPIC_API_KEY') !== null ||
        str(env, 'ANTHROPIC_AUTH_TOKEN') !== null,
      model: str(env, 'ROUTER_CLAUDE_MODEL') ?? 'claude-opus-5-5',
      effort: str(env, 'ROUTER_CLAUDE_EFFORT'),
      promptCache: bool(env, 'ROUTER_CLAUDE_PROMPT_CACHE', true),
      refusalFallback: bool(env, 'ROUTER_CLAUDE_REFUSAL_FALLBACK', true),
      dailyTokenBudget: num(env, 'ROUTER_CLAUDE_DAILY_TOKEN_BUDGET', 0),
    },
    routing: {
      midThreshold: num(env, 'ROUTE_MID_THRESHOLD', 2),
      smartThreshold: num(env, 'ROUTE_SMART_THRESHOLD', 4),
      fallback: bool(env, 'FALLBACK', true),
    },
    cache: {
      enabled: bool(env, 'CACHE_ENABLED', true),
      ttlMs: num(env, 'CACHE_TTL_SECONDS', 86_400) * 1000,
      maxEntries: num(env, 'CACHE_MAX_ENTRIES', 1000),
    },
    compression: {
      mode,
      historyBudgetTokens: num(env, 'HISTORY_TOKEN_BUDGET', 24_000),
    },
    maxOutputTokens: maxOutput > 0 ? maxOutput : null,
    pricing,
  };
}
