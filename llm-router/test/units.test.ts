import assert from 'node:assert/strict';
import { test } from 'node:test';
import { classify } from '../src/classify.js';
import { compactJson, compressMessages, squeezeWhitespace } from '../src/compress.js';
import { HistoryCompactor } from '../src/history.js';
import { toAnthropicMessages } from '../src/providers/claude.js';
import { estimateMessagesTokens } from '../src/tokens.js';
import type { ChatMessage } from '../src/types.js';
import { EASY, HARD } from './helpers.js';

const THRESHOLDS = { midThreshold: 2, smartThreshold: 4 };
const tierOf = (content: string, extra: object = {}) =>
  classify({ messages: [{ role: 'user', content }], ...extra }, THRESHOLDS).tier;

test('classify: everyday prompts stay cheap, real engineering work goes up', () => {
  assert.equal(tierOf(EASY), 'cheap');
  assert.equal(tierOf('이 문장 영어로 번역해줘: 오늘 날씨가 좋네요'), 'cheap');
  assert.equal(tierOf('Summarize this paragraph in one line please.'), 'cheap');
  assert.equal(tierOf('What class of animals are dolphins?'), 'cheap', 'prose that mentions "class" is not code');
  assert.equal(tierOf('결제 서비스의 아키텍처를 설계해줘. 요구사항은 다음과 같아: '.padEnd(200, '가')), 'mid');
  assert.equal(tierOf(HARD), 'smart');
  assert.equal(tierOf('hi', { reasoning_effort: 'high', tools: [{ type: 'function', function: { name: 'x' } }] }), 'mid');
});

test('squeezeWhitespace: trims prose but leaves fenced code untouched', () => {
  const code = '```py\ndef f():   \n\n\n\n    return 1\n```';
  const text = `Intro line with trailing spaces.     \n\n\n\n\nSecond paragraph follows after many blank lines.\n${code}\n\n\n`;
  assert.equal(
    squeezeWhitespace(text),
    `Intro line with trailing spaces.\n\nSecond paragraph follows after many blank lines.\n${code}`,
  );
});

test('compactJson: minifies pretty JSON, refuses numbers that would not round-trip', () => {
  const value = { items: Array.from({ length: 30 }, (_, i) => ({ i, label: `item ${i}` })) };
  assert.equal(compactJson(JSON.stringify(value, null, 2)), JSON.stringify(value));
  const big = `{\n  "id": 12345678901234567890,\n  "pad": "${'x'.repeat(300)}"\n}`;
  assert.equal(compactJson(big), big);
  assert.equal(compactJson('not json at all'.repeat(30)), 'not json at all'.repeat(30));
});

test('compressMessages: dedups repeated tool output, leaves assistant turns alone, is deterministic', () => {
  const output = 'line of tool output\n'.repeat(60);
  const messages: ChatMessage[] = [
    { role: 'user', content: 'read the file' },
    { role: 'assistant', content: 'ok   \n\n\n\nreading', tool_calls: [{ id: 'a', type: 'function', function: { name: 'read', arguments: '{}' } }] },
    { role: 'tool', tool_call_id: 'a', content: output },
    { role: 'assistant', content: null, tool_calls: [{ id: 'b', type: 'function', function: { name: 'read', arguments: '{}' } }] },
    { role: 'tool', tool_call_id: 'b', content: output },
  ];
  const result = compressMessages(messages, 'standard');
  assert.equal(result.messages[1], messages[1], 'assistant message is the same object');
  assert.equal(result.messages[2]!.content, output, 'first occurrence kept verbatim');
  assert.match(String(result.messages[4]!.content), /Identical to the earlier result of tool call a/);
  assert.ok(result.tokensSaved > 200);
  assert.deepEqual(compressMessages(messages, 'standard').messages, result.messages);
  assert.equal(compressMessages(messages, 'off').messages, messages);
  assert.equal(compressMessages(messages, 'lossless').messages[4]!.content, output, 'dedup is not lossless');
});

test('compressMessages: aggressive mode truncates only old, large tool output', () => {
  const big = 'x'.repeat(10_000);
  const messages: ChatMessage[] = [];
  for (let i = 0; i < 12; i++) {
    messages.push({ role: 'user', content: `step ${i}` }, { role: 'tool', tool_call_id: `c${i}`, content: `${i}:${big}` });
  }
  const result = compressMessages(messages, 'aggressive').messages;
  assert.ok(String(result[1]!.content).length < 3000, 'old output truncated');
  assert.match(String(result[1]!.content), /characters omitted by the router/);
  assert.equal(result.at(-1)!.content, messages.at(-1)!.content, 'recent output kept');
});

test('toAnthropicMessages: system lifted out, tool results merged, images converted', () => {
  const { system, messages } = toAnthropicMessages([
    { role: 'system', content: 'Be brief.' },
    { role: 'developer', content: 'Use Korean.' },
    {
      role: 'user',
      content: [
        { type: 'text', text: 'what is this?' },
        { type: 'image_url', image_url: { url: 'data:image/png;base64,AAAA' } },
        { type: 'image_url', image_url: { url: 'https://example.com/a.jpg' } },
      ],
    },
    {
      role: 'assistant',
      content: '',
      tool_calls: [
        { id: 'call.1', type: 'function', function: { name: 'a', arguments: '{"x":1}' } },
        { id: 'call.2', type: 'function', function: { name: 'b', arguments: 'not json' } },
      ],
    },
    { role: 'tool', tool_call_id: 'call.1', content: 'one' },
    { role: 'tool', tool_call_id: 'call.2', content: '' },
  ]);
  assert.equal(system, 'Be brief.\n\nUse Korean.');
  assert.deepEqual(messages, [
    {
      role: 'user',
      content: [
        { type: 'text', text: 'what is this?' },
        { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'AAAA' } },
        { type: 'image', source: { type: 'url', url: 'https://example.com/a.jpg' } },
      ],
    },
    {
      role: 'assistant',
      content: [
        { type: 'tool_use', id: 'call_1', name: 'a', input: { x: 1 } },
        { type: 'tool_use', id: 'call_2', name: 'b', input: {} },
      ],
    },
    {
      role: 'user',
      content: [
        { type: 'tool_result', tool_use_id: 'call_1', content: 'one' },
        { type: 'tool_result', tool_use_id: 'call_2', content: '(empty result)' },
      ],
    },
  ]);
  assert.equal(toAnthropicMessages([{ role: 'assistant', content: 'hi' }]).messages[0]!.role, 'user');
});

test('HistoryCompactor: summarises old turns once and keeps the cut stable across turns', async () => {
  const prompts: string[] = [];
  const compactor = new HistoryCompactor(2000, async (prompt) => {
    prompts.push(prompt);
    return `SUMMARY#${prompts.length}`;
  });
  const turn = (i: number): ChatMessage[] => [
    { role: 'user', content: `question ${i} ${'word '.repeat(150)}` },
    { role: 'assistant', content: `answer ${i} ${'word '.repeat(150)}` },
  ];
  const system: ChatMessage = { role: 'system', content: 'You are helpful.' };
  let messages: ChatMessage[] = [system, ...Array.from({ length: 6 }, (_, i) => turn(i)).flat(), { role: 'user', content: 'latest question' }];

  const first = await compactor.apply(messages);
  assert.equal(prompts.length, 1);
  assert.ok(first.dropped > 0 && first.tokensSaved > 0);
  assert.equal(first.messages[0], system);
  assert.match(String(first.messages[1]!.content), /^\[Summary of the earlier conversation.*\nSUMMARY#1$/s);
  assert.equal(first.messages[2]!.role, 'user', 'kept tail starts at a user turn');
  assert.equal(first.messages.at(-1)!.content, 'latest question');
  assert.ok(estimateMessagesTokens(first.messages) <= 2000);

  // One more turn: still within budget from the remembered cut — no new summary call.
  messages = [...messages, { role: 'assistant', content: 'short answer' }, { role: 'user', content: 'follow-up' }];
  const second = await compactor.apply(messages);
  assert.equal(prompts.length, 1);
  assert.equal(second.dropped, first.dropped);
  assert.deepEqual(second.messages.slice(0, first.messages.length), first.messages, 'prefix unchanged, so prompt caches stay warm');

  // Grow past the budget again: the cut advances and the summary is extended, not rebuilt.
  messages = [...messages, ...turn(7), ...turn(8), ...turn(9), { role: 'user', content: 'newest' }];
  const third = await compactor.apply(messages);
  assert.equal(prompts.length, 2);
  assert.ok(third.dropped > second.dropped);
  assert.match(prompts[1]!, /Summary so far:\nSUMMARY#1/);

  // A failing summariser must never break the request.
  const failing = new HistoryCompactor(2000, async () => {
    throw new Error('summariser down');
  });
  assert.equal((await failing.apply(messages)).messages, messages);
  // Short conversations are left alone.
  assert.equal((await compactor.apply([system, { role: 'user', content: 'hi' }])).dropped, 0);
});
