import type { ProviderName, ToolCall } from './types.js';
import { LruMap } from './util.js';

export interface ToolOrigin {
  provider: ProviderName;
  model: string;
  /** Provider extras for the call (Gemini thought signatures) to re-attach on the way back in. */
  extra?: unknown;
}

/**
 * Remembers which upstream issued each tool call. A tool loop is routed back
 * to the model that started it, and extras that OpenAI-format clients drop
 * are restored before the conversation returns upstream.
 */
export class ToolOrigins {
  private readonly origins = new LruMap<string, ToolOrigin>(5000);

  remember(calls: ToolCall[] | undefined, provider: ProviderName, model: string): void {
    for (const call of calls ?? []) {
      if (call.id) this.origins.set(call.id, { provider, model, extra: call.extra_content });
    }
  }

  get(id: string): ToolOrigin | undefined {
    return this.origins.get(id);
  }
}
