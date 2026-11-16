import { classify } from './classify.js';
import type { Config } from './config.js';
import type { ToolOrigin } from './origins.js';
import type { ChatRequest, ProviderName, Tier } from './types.js';

export interface Candidate {
  provider: ProviderName;
  model: string;
}

export interface Plan {
  /** null when the client named an exact model. */
  tier: Tier | null;
  /** Human-readable explanation, returned in the `x-atuo-route` header. */
  reason: string;
  candidates: Candidate[];
}

export interface RouteContext {
  geminiAvailable: boolean;
  claudeAvailable: boolean;
  /** True when Claude is configured but today's token budget is spent. */
  claudeOverBudget: boolean;
  forcedTier?: Tier;
  /** Upstream that issued the tool calls this request is answering, if known. */
  sticky?: ToolOrigin;
}

const TIER_ALIASES: Record<string, Tier> = {
  cheap: 'cheap',
  fast: 'cheap',
  flash: 'cheap',
  mid: 'mid',
  pro: 'mid',
  smart: 'smart',
  best: 'smart',
};

/**
 * Turns a request into an ordered list of upstreams to try.
 *
 *   cheap -> Gemini cheap -> Gemini mid
 *   mid   -> Gemini mid   -> Gemini cheap
 *   smart -> Claude       -> Gemini mid -> Gemini cheap
 *
 * Claude is only ever reached through the smart tier or by name, so a Gemini
 * outage can never silently move cheap traffic onto premium tokens.
 */
export function planRoute(req: ChatRequest, cfg: Config, ctx: RouteContext): Plan {
  const name = (req.model ?? 'auto').trim().toLowerCase();
  const cheap: Candidate = { provider: 'gemini', model: cfg.gemini.cheapModel };
  const mid: Candidate = { provider: 'gemini', model: cfg.gemini.midModel };
  const claude: Candidate = { provider: 'claude', model: cfg.claude.model };
  const chains: Record<Tier, Candidate[]> = {
    cheap: [cheap, mid],
    mid: [mid, cheap],
    smart: [claude, mid, cheap],
  };

  let tier: Tier | null;
  let reason: string;
  let candidates: Candidate[];

  if (name.startsWith('gemini-')) {
    tier = null;
    reason = `explicit model ${name}`;
    candidates = [{ provider: 'gemini', model: name }, mid, cheap];
  } else if (name.startsWith('claude-')) {
    tier = null;
    reason = `explicit model ${name}`;
    candidates = [{ provider: 'claude', model: name }, mid, cheap];
  } else if (name === 'claude') {
    tier = 'smart';
    reason = 'alias claude';
    candidates = chains.smart;
  } else if (ctx.forcedTier ?? TIER_ALIASES[name]) {
    tier = (ctx.forcedTier ?? TIER_ALIASES[name])!;
    reason = `forced tier ${tier}`;
    candidates = chains[tier];
  } else {
    const result = classify(req, cfg.routing);
    tier = result.tier;
    reason = `auto score ${result.score} (${result.reasons.join(', ') || 'no signals'})`;
    candidates = chains[tier];
    if (name === 'gemini') {
      if (tier === 'smart') tier = 'mid';
      candidates = chains[tier];
      reason += '; gemini only';
    }
  }

  const available = (c: Candidate): boolean =>
    c.provider === 'gemini' ? ctx.geminiAvailable : ctx.claudeAvailable && !ctx.claudeOverBudget;

  if (candidates.some((c) => c.provider === 'claude')) {
    if (!ctx.claudeAvailable) reason += '; Claude not configured';
    else if (ctx.claudeOverBudget) reason += '; Claude daily budget spent';
  }
  candidates = candidates.filter(available);
  // With Gemini unconfigured, Claude is the only provider there is.
  if (!candidates.length && available(claude)) candidates = [claude];

  if (ctx.sticky && tier !== null) {
    const sticky: Candidate = { provider: ctx.sticky.provider, model: ctx.sticky.model };
    if (available(sticky)) {
      candidates = [sticky, ...candidates];
      reason += `; continuing tool loop on ${sticky.model}`;
    }
  }

  const seen = new Set<string>();
  candidates = candidates.filter((c) => {
    const key = `${c.provider}:${c.model}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!cfg.routing.fallback) candidates = candidates.slice(0, 1);

  return { tier, reason, candidates };
}
