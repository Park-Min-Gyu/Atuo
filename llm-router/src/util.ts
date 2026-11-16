import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export function sha256(text: string): string {
  return crypto.createHash('sha256').update(text).digest('hex');
}

export function newId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID().replace(/-/g, '')}`;
}

/** Insertion-ordered map that evicts the least recently used entry past `max`. */
export class LruMap<K, V> {
  private readonly map = new Map<K, V>();
  constructor(private readonly max: number) {}

  get(key: K): V | undefined {
    const value = this.map.get(key);
    if (value !== undefined) {
      this.map.delete(key);
      this.map.set(key, value);
    }
    return value;
  }

  set(key: K, value: V): void {
    this.map.delete(key);
    this.map.set(key, value);
    while (this.map.size > this.max) {
      this.map.delete(this.map.keys().next().value as K);
    }
  }

  delete(key: K): void {
    this.map.delete(key);
  }

  get size(): number {
    return this.map.size;
  }

  entries(): IterableIterator<[K, V]> {
    return this.map.entries();
  }
}

export function loadJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
  } catch {
    return fallback;
  }
}

/** Write-then-rename so a crash mid-write never leaves a truncated file. */
export function saveJson(file: string, data: unknown): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data));
  fs.renameSync(tmp, file);
}

/** Coalesces bursts of saves into one write; `flush()` writes immediately. */
export class DebouncedSaver {
  private timer: NodeJS.Timeout | null = null;
  constructor(
    private readonly file: string | null,
    private readonly snapshot: () => unknown,
    private readonly delayMs = 1000,
  ) {}

  schedule(): void {
    if (!this.file || this.timer) return;
    this.timer = setTimeout(() => this.flush(), this.delayMs);
    this.timer.unref();
  }

  flush(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!this.file) return;
    try {
      saveJson(this.file, this.snapshot());
    } catch (err) {
      console.error(`[atuo] could not save ${this.file}:`, (err as Error).message);
    }
  }
}

export function localDay(now = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
