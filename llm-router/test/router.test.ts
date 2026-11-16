import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { claudeMessage, EASY, HARD, readSse, startRouter, type Harness } from './helpers.js';

let h: Harness;
afterEach(async () => {
  await h?.close();
});

test('easy prompt goes to the cheap Gemini model with low reasoning effort', async () => {
  h = await startRouter();
  const res = await h.chat({ model: 'auto', messages: [{ role: 'user', content: EASY }], user: 'someone' });
  const body: any = await res.json();

  assert.equal(res.status, 200);
  assert.equal(res.headers.get('x-atuo-provider'), 'gemini');
  assert.equal(res.headers.get('x-atuo-tier'), 'cheap');
  assert.equal(body.choices[0].message.content, 'gemini:gemini-2.5-flash');
  assert.equal(h.claude.requests.length, 0);

  const sent = h.gemini.requests[0]!;
  assert.equal(sent.path, '/chat/completions');
  assert.equal(sent.headers.authorization, 'Bearer test-gemini-key');
  assert.equal(sent.body.model, 'gemini-2.5-flash');
  assert.equal(sent.body.reasoning_effort, 'low');
  assert.equal('user' in sent.body, false, 'fields Gemini may reject are dropped');
});

test('hard prompt goes to Claude with a request current models accept', async () => {
  h = await startRouter();
  const res = await h.chat({
    messages: [
      { role: 'system', content: 'You are a careful reviewer.' },
      { role: 'user', content: HARD },
    ],
    temperature: 0.2,
    tool_choice: 'required',
    tools: [{ type: 'function', function: { name: 'run', parameters: { type: 'object', properties: {} } } }],
  });
  const body: any = await res.json();

  assert.equal(res.headers.get('x-atuo-provider'), 'claude');
  assert.equal(res.headers.get('x-atuo-tier'), 'smart');
  assert.equal(body.choices[0].message.content, 'claude answer');
  assert.deepEqual(body.usage, { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 });
  assert.equal(h.gemini.requests.length, 0);

  const sent = h.claude.requests[0]!;
  assert.match(String(sent.headers['x-api-key'] ?? sent.headers.authorization), /test-claude-key/);
  assert.equal(sent.body.model, 'claude-opus-5-5');
  assert.equal(sent.body.system, 'You are a careful reviewer.');
  assert.equal(sent.body.max_tokens, 16000);
  assert.equal('temperature' in sent.body, false, 'sampling params are rejected by current models');
  assert.deepEqual(sent.body.tool_choice, { type: 'auto' }, 'forced tool use is not supported');
  assert.deepEqual(sent.body.cache_control, { type: 'ephemeral' });
  assert.equal(sent.body.fallbacks, 'default');
  assert.match(String(sent.headers['anthropic-beta']), /server-side-fallback-2026-07-01/);
});

test('identical request is answered from the cache without calling upstream', async () => {
  h = await startRouter();
  const request = { messages: [{ role: 'user', content: EASY }] };
  const first: any = await (await h.chat(request)).json();
  const second = await h.chat(request);
  const secondBody: any = await second.json();

  assert.equal(h.gemini.requests.length, 1);
  assert.equal(second.headers.get('x-atuo-cache'), 'hit');
  assert.equal(secondBody.choices[0].message.content, first.choices[0].message.content);

  const stats = h.app.stats();
  assert.equal(stats.usage.days[stats.usage.today]!.cacheHits, 1);

  await h.chat(request, { 'x-atuo-cache': 'off' });
  assert.equal(h.gemini.requests.length, 2, 'x-atuo-cache: off bypasses the cache');
});

test('cached answers are replayed for streaming requests too', async () => {
  h = await startRouter();
  const request = { messages: [{ role: 'user', content: EASY }] };
  await (await h.chat(request)).text();
  const { events, done } = await readSse(await h.chat({ ...request, stream: true }));

  assert.equal(h.gemini.requests.length, 1);
  assert.equal(events[0].choices[0].delta.content, 'gemini:gemini-2.5-flash');
  assert.equal(events.at(-1).choices[0].finish_reason, 'stop');
  assert.equal(done, true);
});

test('a rate-limited cheap model falls over to the next Gemini model, never to Claude', async () => {
  h = await startRouter();
  const ok = h.gemini.respond;
  h.gemini.respond = (req, res) => {
    if (req.body.model === 'gemini-2.5-flash') {
      res.writeHead(429, { 'content-type': 'application/json', 'retry-after': '7' });
      res.end(JSON.stringify([{ error: { code: 429, message: 'quota exceeded', status: 'RESOURCE_EXHAUSTED' } }]));
    } else ok(req, res);
  };
  const res = await h.chat({ messages: [{ role: 'user', content: EASY }] });

  assert.equal(res.status, 200);
  assert.equal(res.headers.get('x-atuo-model'), 'gemini-2.5-pro');
  assert.equal(h.claude.requests.length, 0);
  assert.deepEqual(h.app.stats().cooldowns.map((c) => c.upstream), ['gemini:gemini-2.5-flash']);

  // While flash cools down, the next request skips straight to pro.
  await h.chat({ messages: [{ role: 'user', content: '반가워요!' }] });
  assert.deepEqual(h.gemini.requests.map((r) => r.body.model), ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.5-pro']);
});

test('Claude outage falls over to Gemini; total failure reports the first error', async () => {
  h = await startRouter();
  h.claude.respond = (_req, res) => {
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: 'invalid x-api-key' } }));
  };
  const res = await h.chat({ messages: [{ role: 'user', content: HARD }] });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('x-atuo-provider'), 'gemini');
  assert.equal(res.headers.get('x-atuo-model'), 'gemini-2.5-pro');

  h.gemini.respond = (_req, res) => {
    res.writeHead(500, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'boom' } }));
  };
  const failed = await h.chat({ model: 'cheap', messages: [{ role: 'user', content: 'another question' }] });
  assert.equal(failed.status, 500);
  assert.equal(((await failed.json()) as any).error.message, 'boom');
});

test('Gemini streaming is relayed; the usage chunk only appears on request', async () => {
  h = await startRouter();
  const request = { stream: true, messages: [{ role: 'user', content: EASY }] };
  const plain = await readSse(await h.chat(request, { 'x-atuo-cache': 'off' }));
  assert.equal(plain.events.map((e) => e.choices[0]?.delta.content ?? '').join(''), 'Hello');
  assert.equal(plain.done, true);
  assert.equal(plain.events.some((e) => e.usage), false);

  const withUsage = await readSse(
    await h.chat({ ...request, stream_options: { include_usage: true } }, { 'x-atuo-cache': 'off' }),
  );
  assert.deepEqual(withUsage.events.at(-1).usage, { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 });
  assert.equal(h.gemini.requests[0]!.body.stream_options.include_usage, true, 'usage is always metered');

  const today = h.app.stats().usage;
  assert.equal(today.days[today.today]!.models['gemini-2.5-flash']!.inputTokens, 14);
});

test('Claude streaming converts text and tool calls to OpenAI chunks', async () => {
  h = await startRouter();
  const res = await h.chat({ model: 'smart', stream: true, messages: [{ role: 'user', content: 'weather in Seoul?' }] });
  const { events, done } = await readSse(res);

  assert.equal(res.headers.get('x-atuo-provider'), 'claude');
  assert.equal(events.map((e) => e.choices[0].delta.content ?? '').join(''), 'Checking.');
  const calls = events.flatMap((e) => e.choices[0].delta.tool_calls ?? []);
  assert.equal(calls[0].id, 'toolu_1');
  assert.equal(calls[0].function.name, 'get_weather');
  assert.equal(calls.map((c: any) => c.function.arguments).join(''), '{"city":"Seoul"}');
  assert.equal(events.at(-1).choices[0].finish_reason, 'tool_calls');
  assert.equal(done, true);
  assert.equal(h.claude.requests[0]!.body.max_tokens, 64000);
});

test('a tool loop returns to the model that issued the call', async () => {
  h = await startRouter();
  h.claude.respond = (req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    const content = req.body.messages.length === 1
      ? [{ type: 'tool_use', id: 'toolu_9', name: 'lookup', input: { q: 'x' } }]
      : [{ type: 'text', text: 'done' }];
    res.end(JSON.stringify(claudeMessage(content, req.body.messages.length === 1 ? 'tool_use' : 'end_turn')));
  };
  const first: any = await (await h.chat({ model: "smart", messages: [{ role: "user", content: "look it up" }] })).json();
  const call = first.choices[0].message.tool_calls[0];
  assert.deepEqual(JSON.parse(call.function.arguments), { q: 'x' });

  // Model "auto" + a short prompt would classify as cheap, but the loop stays on Claude.
  const res = await h.chat({
    model: 'auto',
    messages: [
      { role: 'user', content: 'look it up' },
      { role: 'assistant', content: null, tool_calls: [call] },
      { role: 'tool', tool_call_id: call.id, content: 'result text' },
    ],
  });
  assert.equal(res.headers.get('x-atuo-provider'), 'claude');
  assert.deepEqual(h.claude.requests[1]!.body.messages, [
    { role: 'user', content: [{ type: 'text', text: 'look it up' }] },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'toolu_9', name: 'lookup', input: { q: 'x' } }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'toolu_9', content: 'result text' }] },
  ]);
  assert.equal(h.gemini.requests.length, 0);
});

test('Claude daily token budget moves smart traffic to Gemini', async () => {
  h = await startRouter({ ROUTER_CLAUDE_DAILY_TOKEN_BUDGET: '25' });
  const first = await h.chat({ model: 'smart', messages: [{ role: 'user', content: 'first' }] });
  assert.equal(first.headers.get('x-atuo-provider'), 'claude'); // uses 28 tokens

  const second = await h.chat({ model: 'smart', messages: [{ role: 'user', content: 'second' }] });
  assert.equal(second.headers.get('x-atuo-provider'), 'gemini');
  assert.equal(second.headers.get('x-atuo-model'), 'gemini-2.5-pro');
  assert.match(second.headers.get('x-atuo-route')!, /budget spent/);
});

test('without a Claude key the smart tier uses Gemini', async () => {
  h = await startRouter({}, false);
  const res = await h.chat({ messages: [{ role: 'user', content: HARD }] });
  assert.equal(res.headers.get('x-atuo-tier'), 'smart');
  assert.equal(res.headers.get('x-atuo-model'), 'gemini-2.5-pro');
  assert.deepEqual(((await (await fetch(`${h.url}/v1/models`)).json()) as any).data.map((m: any) => m.id), [
    'auto', 'cheap', 'mid', 'smart', 'gemini-2.5-flash', 'gemini-2.5-pro',
  ]);
});

test('ROUTER_API_KEY protects the API but not the health check', async () => {
  h = await startRouter({ ROUTER_API_KEY: 'secret' });
  const body = { messages: [{ role: 'user', content: EASY }] };
  assert.equal((await h.chat(body)).status, 401);
  assert.equal((await h.chat(body, { authorization: 'Bearer wrong' })).status, 401);
  assert.equal((await h.chat(body, { authorization: 'Bearer secret' })).status, 200);
  assert.equal((await fetch(`${h.url}/stats`)).status, 401);
  assert.equal((await fetch(`${h.url}/health`)).status, 200);
  assert.equal(h.gemini.requests.length, 1);
});

test('bad requests are rejected before any upstream call', async () => {
  h = await startRouter();
  assert.equal((await h.chat({ messages: [] })).status, 400);
  assert.equal((await h.chat({ messages: [{ role: 'user', content: 'x' }] }, { 'x-atuo-tier': 'huge' })).status, 400);
  const raw = await fetch(`${h.url}/v1/chat/completions`, { method: 'POST', body: '{nope' });
  assert.equal(raw.status, 400);
  assert.equal(h.gemini.requests.length + h.claude.requests.length, 0);
});

test('compression shrinks what is sent upstream and is counted', async () => {
  h = await startRouter();
  const pretty = JSON.stringify({ rows: Array.from({ length: 40 }, (_, i) => ({ id: i, name: `row ${i}` })) }, null, 2);
  const call = { id: 'call_1', type: 'function', function: { name: 'load', arguments: '{}' } };
  await h.chat({
    model: 'cheap',
    messages: [
      { role: 'user', content: 'load the rows' },
      { role: 'assistant', content: null, tool_calls: [call] },
      { role: 'tool', tool_call_id: 'call_1', content: pretty },
    ],
  });
  const sent = h.gemini.requests[0]!.body.messages[2].content;
  assert.equal(sent, JSON.stringify(JSON.parse(pretty)));
  const stats = h.app.stats().usage;
  assert.ok(stats.days[stats.today]!.compressionSavedTokens > 100);
});
