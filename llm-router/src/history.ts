import { contentText, estimateMessagesTokens, estimateTokens } from './tokens.js';
import type { ChatMessage } from './types.js';
import { LruMap, sha256 } from './util.js';

export type Summarizer = (prompt: string) => Promise<string>;

export interface HistoryResult {
  messages: ChatMessage[];
  /** Messages replaced by the summary (0 = untouched). */
  dropped: number;
  tokensSaved: number;
}

interface ConversationState {
  /** How many non-system messages are folded into `summary`. */
  cut: number;
  summary: string;
  /** Hash of the folded messages, so a branched conversation never reuses a stale summary. */
  prefixHash: string;
}

const KEEP_FRACTION = 0.6;
const TOOL_OUTPUT_CHARS = 1500;

function isSystem(message: ChatMessage): boolean {
  return message.role === 'system' || message.role === 'developer';
}

function transcript(messages: ChatMessage[]): string {
  return messages
    .map((m) => {
      let text = contentText(m.content);
      if (m.role === 'tool' && text.length > TOOL_OUTPUT_CHARS) {
        text = `${text.slice(0, TOOL_OUTPUT_CHARS)} [truncated]`;
      }
      const calls = (m.tool_calls ?? [])
        .map((c) => `[calls ${c.function.name}(${c.function.arguments.slice(0, 300)})]`)
        .join(' ');
      return `${m.role.toUpperCase()}: ${[text, calls].filter(Boolean).join(' ')}`;
    })
    .join('\n\n');
}

function summaryPrompt(previous: string, dropped: ChatMessage[]): string {
  return [
    'Condense the earlier part of a conversation so another assistant can continue it without re-reading it.',
    'Keep every fact, decision, name, number, file path, code identifier, user preference and unfinished task.',
    'Drop greetings and repetition. Write in the language the conversation uses. At most 400 words, plain text.',
    previous ? `\nSummary so far:\n${previous}` : '',
    `\nMessages to fold into the summary:\n${transcript(dropped)}`,
  ].join('\n');
}

/**
 * Keeps long conversations under a token budget by replacing the oldest turns
 * with a summary written by the cheap model: cheap tokens spent once to avoid
 * re-sending premium tokens on every later turn.
 *
 * The cut point is remembered per conversation and only moves when the kept
 * tail outgrows the budget again, so the prefix sent upstream stays stable
 * (and prompt-cacheable) between moves. Any failure leaves the history as is.
 */
export class HistoryCompactor {
  private readonly conversations = new LruMap<string, ConversationState>(300);

  constructor(
    private readonly budgetTokens: number,
    private readonly summarize: Summarizer,
  ) {}

  async apply(messages: ChatMessage[]): Promise<HistoryResult> {
    const untouched: HistoryResult = { messages, dropped: 0, tokensSaved: 0 };
    const total = estimateMessagesTokens(messages);
    if (this.budgetTokens <= 0 || total <= this.budgetTokens) return untouched;

    const system = messages.filter(isSystem);
    const body = messages.filter((m) => !isSystem(m));
    if (body.length < 4) return untouched;

    const key = sha256(`${system.map((m) => contentText(m.content)).join('\n')}\n${contentText(body[0]!.content)}`);
    const previous = this.conversations.get(key);
    const systemTokens = estimateMessagesTokens(system);
    const tailTokens = (cut: number): number => estimateMessagesTokens(body.slice(cut));
    const fits = (state: ConversationState): boolean =>
      systemTokens + estimateTokens(state.summary) + tailTokens(state.cut) <= this.budgetTokens;

    const prefixHash = (cut: number): string => sha256(JSON.stringify(body.slice(0, cut)));
    let state =
      previous && previous.cut < body.length && previous.prefixHash === prefixHash(previous.cut)
        ? previous
        : undefined;
    if (!state || !fits(state)) {
      // Cut only at a user message so no tool result is separated from its call.
      const keepTokens = this.budgetTokens * KEEP_FRACTION;
      let cut = -1;
      for (let i = (state?.cut ?? 0) + 1; i < body.length; i++) {
        if (body[i]!.role !== 'user') continue;
        cut = i;
        if (tailTokens(i) <= keepTokens) break;
      }
      // Never fold the latest user turn itself into the summary.
      const lastUser = body.map((m) => m.role).lastIndexOf('user');
      if (cut > lastUser) cut = lastUser;
      if (cut <= (state?.cut ?? 0)) return untouched;

      try {
        const summary = (await this.summarize(summaryPrompt(state?.summary ?? '', body.slice(state?.cut ?? 0, cut)))).trim();
        if (!summary) return untouched;
        state = { cut, summary, prefixHash: prefixHash(cut) };
        this.conversations.set(key, state);
      } catch (err) {
        console.error('[atuo] history summary failed, sending full history:', (err as Error).message);
        return untouched;
      }
    }

    const summaryMessage: ChatMessage = {
      role: 'user',
      content: `[Summary of the earlier conversation — ${state.cut} messages condensed by the router]\n${state.summary}`,
    };
    const compacted = [...system, summaryMessage, ...body.slice(state.cut)];
    const after = estimateMessagesTokens(compacted);
    if (after >= total) return untouched;
    return { messages: compacted, dropped: state.cut, tokensSaved: total - after };
  }
}
