import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { RouterApp } from '../src/app.js';
import { loadConfig, type Config } from '../src/config.js';
import { createServer } from '../src/server.js';

export interface Recorded {
  path: string;
  headers: http.IncomingHttpHeaders;
  body: any;
}

export interface Mock {
  url: string;
  requests: Recorded[];
  /** Replace to change how the next requests are answered. */
  respond: (req: Recorded, res: http.ServerResponse) => void;
  close: () => Promise<void>;
}

function listen(server: http.Server): Promise<string> {
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`));
  });
}

async function startMock(respond: Mock['respond']): Promise<Mock> {
  const mock: Mock = { url: '', requests: [], respond, close: async () => {} };
  const server = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const recorded: Recorded = { path: req.url ?? '', headers: req.headers, body: raw ? JSON.parse(raw) : null };
      if (req.method !== 'POST') {
        // e.g. the startup model-list probe; the mocks only implement completions
        res.writeHead(404).end();
        return;
      }
      mock.requests.push(recorded);
      mock.respond(recorded, res);
    });
  });
  mock.url = await listen(server);
  mock.close = () =>
    new Promise((resolve) => {
      server.closeAllConnections();
      server.close(() => resolve());
    });
  return mock;
}

function sse(res: http.ServerResponse, events: Array<string>): void {
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const event of events) res.write(event);
  res.end();
}

// ---- Gemini (OpenAI-compatible) ---------------------------------------------

export function geminiJson(text: string, model = 'gemini-2.5-flash') {
  return {
    id: 'g1',
    object: 'chat.completion',
    created: 1,
    model,
    choices: [{ index: 0, message: { role: 'assistant', content: text }, finish_reason: 'stop' }],
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
  };
}

export function startGemini(): Promise<Mock> {
  return startMock((req, res) => {
    if (!req.body.stream) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(geminiJson(`gemini:${req.body.model}`, req.body.model)));
      return;
    }
    const chunk = (delta: object, finish: string | null = null, usage?: object) =>
      `data: ${JSON.stringify({ id: 'g1', object: 'chat.completion.chunk', created: 1, model: req.body.model, choices: [{ index: 0, delta, finish_reason: finish }], ...(usage ? { usage } : {}) })}\n\n`;
    sse(res, [
      chunk({ role: 'assistant', content: 'Hel' }),
      chunk({ content: 'lo' }),
      chunk({}, 'stop', { prompt_tokens: 7, completion_tokens: 2, total_tokens: 9 }),
      'data: [DONE]\n\n',
    ]);
  });
}

// ---- Anthropic Messages API -------------------------------------------------

const CLAUDE_USAGE = { input_tokens: 20, output_tokens: 8, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 };

export function claudeMessage(content: object[], stopReason = 'end_turn', model = 'claude-opus-5-5') {
  return {
    id: 'msg_1',
    type: 'message',
    role: 'assistant',
    model,
    content,
    stop_reason: stopReason,
    stop_sequence: null,
    stop_details: null,
    usage: CLAUDE_USAGE,
  };
}

function claudeEvent(type: string, data: object): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`;
}

/** Streams a text block followed by one tool call, the way the Messages API does. */
export function claudeToolStream(res: http.ServerResponse, model: string): void {
  sse(res, [
    claudeEvent('message_start', { message: { ...claudeMessage([], undefined, model), stop_reason: null } }),
    claudeEvent('content_block_start', { index: 0, content_block: { type: 'text', text: '' } }),
    claudeEvent('content_block_delta', { index: 0, delta: { type: 'text_delta', text: 'Checking.' } }),
    claudeEvent('content_block_stop', { index: 0 }),
    claudeEvent('content_block_start', { index: 1, content_block: { type: 'tool_use', id: 'toolu_1', name: 'get_weather', input: {} } }),
    claudeEvent('content_block_delta', { index: 1, delta: { type: 'input_json_delta', partial_json: '{"city":' } }),
    claudeEvent('content_block_delta', { index: 1, delta: { type: 'input_json_delta', partial_json: '"Seoul"}' } }),
    claudeEvent('content_block_stop', { index: 1 }),
    claudeEvent('message_delta', { delta: { stop_reason: 'tool_use', stop_sequence: null }, usage: { output_tokens: 8 } }),
    claudeEvent('message_stop', {}),
  ]);
}

export function startClaude(): Promise<Mock> {
  return startMock((req, res) => {
    if (req.body.stream) {
      claudeToolStream(res, req.body.model);
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(claudeMessage([{ type: 'text', text: 'claude answer' }], 'end_turn', req.body.model)));
  });
}

// ---- Router under test -------------------------------------------------------

export interface Harness {
  url: string;
  app: RouterApp;
  cfg: Config;
  gemini: Mock;
  claude: Mock;
  chat: (body: object, headers?: Record<string, string>) => Promise<Response>;
  close: () => Promise<void>;
}

export async function startRouter(env: Record<string, string> = {}, withClaude = true): Promise<Harness> {
  const gemini = await startGemini();
  const claude = await startClaude();
  const cfg = loadConfig({
    GEMINI_API_KEY: 'test-gemini-key',
    GEMINI_BASE_URL: gemini.url,
    DATA_DIR: 'none',
    ...env,
  });
  if (withClaude) {
    cfg.claude.enabled = true;
    cfg.claude.apiKey = 'test-claude-key';
    cfg.claude.baseUrl = claude.url;
  }
  const app = new RouterApp(cfg);
  const server = createServer(app);
  const url = await listen(server);
  return {
    url,
    app,
    cfg,
    gemini,
    claude,
    chat: (body, headers = {}) =>
      fetch(`${url}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...headers },
        body: JSON.stringify(body),
      }),
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      await gemini.close();
      await claude.close();
    },
  };
}

export const EASY = '안녕! 오늘 기분 어때?';
export const HARD = '이 코드 디버그해줘. 동시성 버그가 있는 것 같아.\n```js\nlet count = 0;\nasync function inc() { const v = count; await tick(); count = v + 1; }\n```';

/** Parses an SSE body into its JSON events (dropping the [DONE] marker). */
export async function readSse(res: Response): Promise<{ events: any[]; done: boolean }> {
  const text = await res.text();
  const lines = text.split('\n').filter((l) => l.startsWith('data: ')).map((l) => l.slice(6));
  return { events: lines.filter((l) => l !== '[DONE]').map((l) => JSON.parse(l)), done: lines.includes('[DONE]') };
}
