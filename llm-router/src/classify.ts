import { contentText, estimateMessagesTokens } from './tokens.js';
import type { ChatRequest, Tier } from './types.js';

export interface Classification {
  tier: Tier;
  score: number;
  reasons: string[];
}

// Syntax, not vocabulary: these essentially never appear in ordinary prose.
const CODE_MARKERS: readonly RegExp[] = [
  /```/,
  /\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=/,
  /\bfunction\s*\*?\s*[A-Za-z_$][\w$]*\s*\(/,
  /\bdef\s+[A-Za-z_]\w*\s*\(/,
  /^[ \t]*class\s+[A-Za-z_$][\w$]*\s*[({:]/m,
  /\bimport\s[^\n;]{0,200}?\bfrom\s*['"][^'"\n]+['"]/,
  /^[ \t]*from\s+[A-Za-z_.][\w.]*\s+import\s+\S/m,
  /^[ \t]*#\s*include\s*[<"]/m,
  /^\s*at\s+\S.*\(.*:\d+:\d+\)/m,
  /Traceback \(most recent call last\)/,
  /\b[\w./-]+\.(?:[jt]sx?|py|go|rs|java|kt|swift|cs|cpp|sql)\s*:\s*\d+/,
];

const HARD_WORDS: readonly RegExp[] = [
  /아키텍처|설계|리팩터|리팩토링|디버그|디버깅|최적화|증명|알고리즘|동시성|취약점|마이그레이션|단계별|심층|트레이드오프|근본 ?원인/,
  /\b(?:architect(?:ure)?|refactor(?:ing)?|debug(?:ging)?|optimi[sz]e|prove|proof|algorithm|concurren(?:cy|t)|race condition|vulnerabilit(?:y|ies)|migration|trade-?offs?|step[- ]by[- ]step|root cause|in[- ]depth)\b/i,
];

const EASY_WORDS: readonly RegExp[] = [
  /번역|요약|맞춤법|교정|한 ?줄|간단히|짧게/,
  /\b(?:translate|translation|summari[sz]e|tl;?dr|proofread|rephrase|paraphrase|grammar|spelling|one[- ]liner|briefly)\b/i,
];

const SHORT_CHARS = 120;
const MAX_SCAN_CHARS = 6000;

/**
 * Cheap, deterministic difficulty score for one request. Errs toward the
 * cheaper tier: a hard request that lands on a cheap model can be re-asked
 * with `model: "smart"`, while every false "hard" silently spends premium tokens.
 */
export function classify(
  req: ChatRequest,
  thresholds: { midThreshold: number; smartThreshold: number },
): Classification {
  const reasons: string[] = [];
  let score = 0;

  const lastUser = [...req.messages].reverse().find((m) => m.role === 'user');
  const text = contentText(lastUser?.content).slice(0, MAX_SCAN_CHARS);
  const hasCode = CODE_MARKERS.some((re) => re.test(text));

  if (hasCode) {
    score += 2;
    reasons.push('code +2');
  }
  if (req.tools?.length) {
    score += 2;
    reasons.push('tools +2');
  }

  const tokens = estimateMessagesTokens(req.messages);
  if (tokens > 8000) {
    score += 2;
    reasons.push('long context +2');
  } else if (tokens > 2000) {
    score += 1;
    reasons.push('medium context +1');
  }

  if (HARD_WORDS.some((re) => re.test(text))) {
    score += 2;
    reasons.push('hard keyword +2');
  }
  if (EASY_WORDS.some((re) => re.test(text))) {
    score -= 2;
    reasons.push('easy keyword -2');
  }
  if (text.length < SHORT_CHARS && !hasCode) {
    score -= 1;
    reasons.push('short prompt -1');
  }

  const effort = req.reasoning_effort?.toLowerCase();
  if (effort === 'high' || effort === 'xhigh' || effort === 'max') {
    score += 2;
    reasons.push('high effort +2');
  } else if (effort === 'low' || effort === 'minimal' || effort === 'none') {
    score -= 1;
    reasons.push('low effort -1');
  }

  const tier: Tier =
    score >= thresholds.smartThreshold ? 'smart' : score >= thresholds.midThreshold ? 'mid' : 'cheap';
  return { tier, score, reasons };
}
