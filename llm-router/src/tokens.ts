import type { ChatMessage } from './types.js';

/**
 * Rough token estimate used for routing and budgets only — real usage numbers
 * always come from the provider. ASCII averages ~4 chars/token; Hangul and
 * other non-ASCII text runs closer to 1.5 chars/token.
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let ascii = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) < 128) ascii++;
  }
  return Math.ceil(ascii / 4 + (text.length - ascii) / 1.5);
}

/** All text in a message's content (image parts contribute nothing). */
export function contentText(content: ChatMessage['content']): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((part) => part.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text as string)
    .join('\n');
}

const IMAGE_TOKENS = 300;
const MESSAGE_OVERHEAD = 4;

export function messageTokens(message: ChatMessage): number {
  let tokens = MESSAGE_OVERHEAD + estimateTokens(contentText(message.content));
  if (Array.isArray(message.content)) {
    tokens += message.content.filter((part) => part.type === 'image_url').length * IMAGE_TOKENS;
  }
  for (const call of message.tool_calls ?? []) {
    tokens += estimateTokens(call.function.name) + estimateTokens(call.function.arguments);
  }
  return tokens;
}

export function estimateMessagesTokens(messages: ChatMessage[]): number {
  let total = 0;
  for (const message of messages) total += messageTokens(message);
  return total;
}
