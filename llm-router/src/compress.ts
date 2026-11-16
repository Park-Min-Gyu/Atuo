import type { CompressionMode } from './config.js';
import { estimateTokens } from './tokens.js';
import type { ChatMessage } from './types.js';
import { sha256 } from './util.js';

export interface CompressionResult {
  messages: ChatMessage[];
  charsBefore: number;
  charsAfter: number;
  tokensSaved: number;
  /** Characters removed per engine. */
  byEngine: Record<string, number>;
}

// Every engine is a pure function of the message list, so an unchanged
// conversation prefix always compresses to the same bytes. That keeps the
// providers' prompt caches (a prefix match) warm across turns.

const ENGINES: Record<CompressionMode, readonly string[]> = {
  off: [],
  lossless: ['whitespace', 'json'],
  standard: ['whitespace', 'json', 'dedup'],
  aggressive: ['whitespace', 'json', 'dedup', 'truncate'],
};

function textOf(message: ChatMessage): string {
  if (typeof message.content === 'string') return message.content;
  if (!Array.isArray(message.content)) return '';
  return message.content.map((p) => (p.type === 'text' ? (p.text ?? '') : '')).join('');
}

function mapText(message: ChatMessage, fn: (text: string) => string): ChatMessage {
  if (typeof message.content === 'string') {
    const next = fn(message.content);
    return next === message.content ? message : { ...message, content: next };
  }
  if (!Array.isArray(message.content)) return message;
  let changed = false;
  const parts = message.content.map((part) => {
    if (part.type !== 'text' || typeof part.text !== 'string') return part;
    const next = fn(part.text);
    if (next === part.text) return part;
    changed = true;
    return { ...part, text: next };
  });
  return changed ? { ...message, content: parts } : message;
}

/**
 * Trims trailing spaces and collapses runs of blank lines, outside fenced code
 * blocks only — inside a fence whitespace can be significant (diffs, Python).
 */
export function squeezeWhitespace(text: string): string {
  if (text.length < 80) return text;
  const out: string[] = [];
  let inFence = false;
  let blankRun = 0;
  for (const raw of text.split('\n')) {
    const isFence = /^\s*(?:```|~~~)/.test(raw);
    if (inFence && !isFence) {
      out.push(raw);
      continue;
    }
    if (isFence) inFence = !inFence;
    const line = raw.replace(/[ \t\r]+$/, '');
    if (line === '') {
      blankRun++;
      if (blankRun > 1) continue;
    } else {
      blankRun = 0;
    }
    out.push(line);
  }
  return out.join('\n').replace(/\n+$/, '');
}

/** Minifies pretty-printed JSON. Skips anything whose numbers might not round-trip. */
export function compactJson(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length < 200 || !/^[[{]/.test(trimmed) || !/\n\s+/.test(trimmed)) return text;
  if (/\d{16,}/.test(trimmed)) return text;
  try {
    const compact = JSON.stringify(JSON.parse(trimmed));
    return compact.length < trimmed.length ? compact : text;
  } catch {
    return text;
  }
}

const DEDUP_MIN_CHARS = 500;
const TRUNCATE_MIN_CHARS = 3000;
const TRUNCATE_HEAD = 1500;
const TRUNCATE_TAIL = 800;
const TRUNCATE_KEEP_RECENT = 8;
const TRUNCATE_STEP = 8;

function truncateMiddle(text: string): string {
  if (text.length <= TRUNCATE_MIN_CHARS) return text;
  const omitted = text.length - TRUNCATE_HEAD - TRUNCATE_TAIL;
  return `${text.slice(0, TRUNCATE_HEAD)}\n[... ${omitted} characters omitted by the router to save tokens ...]\n${text.slice(-TRUNCATE_TAIL)}`;
}

export function compressMessages(messages: ChatMessage[], mode: CompressionMode): CompressionResult {
  const engines = ENGINES[mode];
  const byEngine: Record<string, number> = {};
  const charsBefore = messages.reduce((sum, m) => sum + textOf(m).length, 0);
  if (!engines.length) {
    return { messages, charsBefore, charsAfter: charsBefore, tokensSaved: 0, byEngine };
  }

  const apply = (engine: string, message: ChatMessage, fn: (t: string) => string): ChatMessage => {
    const next = mapText(message, fn);
    if (next !== message) {
      byEngine[engine] = (byEngine[engine] ?? 0) + textOf(message).length - textOf(next).length;
    }
    return next;
  };

  // Old tool output is truncated only behind a boundary that advances in steps,
  // so the cached prefix is invalidated once per step rather than on every turn.
  const truncateBefore =
    Math.floor(Math.max(0, messages.length - TRUNCATE_KEEP_RECENT) / TRUNCATE_STEP) * TRUNCATE_STEP;
  const seenToolOutputs = new Map<string, string>();
  let tokensSaved = 0;

  const result = messages.map((original, index) => {
    let message = original;
    // Tool output and the model's own turns stay byte-exact apart from the
    // engines below: coding agents match on them character for character.
    if (engines.includes('whitespace') && (message.role === 'user' || message.role === 'system' || message.role === 'developer')) {
      message = apply('whitespace', message, squeezeWhitespace);
    }
    if (message.role === 'tool') {
      if (engines.includes('json')) message = apply('json', message, compactJson);
      if (engines.includes('dedup')) {
        const text = textOf(message);
        if (text.length >= DEDUP_MIN_CHARS) {
          const hash = sha256(text);
          const firstCallId = seenToolOutputs.get(hash);
          if (firstCallId !== undefined) {
            message = apply('dedup', message, () =>
              `[Identical to the earlier result of tool call ${firstCallId}; omitted by the router to save tokens]`,
            );
          } else {
            seenToolOutputs.set(hash, message.tool_call_id ?? `#${index}`);
          }
        }
      }
      if (engines.includes('truncate') && index < truncateBefore) {
        message = apply('truncate', message, truncateMiddle);
      }
    }
    if (message !== original) {
      tokensSaved += estimateTokens(textOf(original)) - estimateTokens(textOf(message));
    }
    return message;
  });

  const charsAfter = result.reduce((sum, m) => sum + textOf(m).length, 0);
  return { messages: result, charsBefore, charsAfter, tokensSaved: Math.max(0, tokensSaved), byEngine };
}
