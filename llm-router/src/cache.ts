import type { ChatCompletion, ChatMessage, ChatRequest } from './types.js';
import { DebouncedSaver, loadJson, sha256 } from './util.js';

export interface CacheEntry {
  completion: ChatCompletion;
  provider: string;
  at: number;
}

interface CacheOptions {
  enabled: boolean;
  ttlMs: number;
  maxEntries: number;
  file: string | null;
}

/**
 * Exact-match response cache: an identical request is answered from disk for
 * zero tokens. Entries live in insertion order, so the first key is the oldest.
 */
export class ResponseCache {
  private readonly entries: Map<string, CacheEntry>;
  private readonly saver: DebouncedSaver;
  hits = 0;
  misses = 0;

  constructor(private readonly opts: CacheOptions) {
    const stored = opts.file ? loadJson<Record<string, CacheEntry>>(opts.file, {}) : {};
    this.entries = new Map(Object.entries(stored));
    this.saver = new DebouncedSaver(opts.file, () => Object.fromEntries(this.entries), 2000);
  }

  get enabled(): boolean {
    return this.opts.enabled;
  }

  get size(): number {
    return this.entries.size;
  }

  /** Everything that can change the answer, and nothing that cannot (stream, user ids…). */
  static key(req: ChatRequest, messages: ChatMessage[]): string {
    return sha256(
      JSON.stringify([
        (req.model ?? 'auto').toLowerCase(),
        messages,
        req.tools ?? null,
        req.tool_choice ?? null,
        req.response_format ?? null,
        req.temperature ?? null,
        req.top_p ?? null,
        req.max_tokens ?? req.max_completion_tokens ?? null,
        req.stop ?? null,
        req.reasoning_effort ?? null,
        req.seed ?? null,
      ]),
    );
  }

  get(key: string): CacheEntry | undefined {
    if (!this.opts.enabled) return undefined;
    const entry = this.entries.get(key);
    if (entry && Date.now() - entry.at > this.opts.ttlMs) {
      this.entries.delete(key);
      this.saver.schedule();
    } else if (entry) {
      this.hits++;
      return entry;
    }
    this.misses++;
    return undefined;
  }

  set(key: string, entry: CacheEntry): void {
    if (!this.opts.enabled) return;
    this.entries.delete(key);
    this.entries.set(key, entry);
    while (this.entries.size > this.opts.maxEntries) {
      this.entries.delete(this.entries.keys().next().value as string);
    }
    this.saver.schedule();
  }

  clear(): void {
    this.entries.clear();
    this.saver.flush();
  }

  flush(): void {
    this.saver.flush();
  }
}
