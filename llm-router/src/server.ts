import crypto from 'node:crypto';
import http from 'node:http';
import { RequestError, type ChatMeta, type ChatOptions, type RouterApp } from './app.js';
import { COMPRESSION_MODES, type CompressionMode } from './config.js';
import { DASHBOARD_HTML } from './dashboard.js';
import { UpstreamError, type ChatRequest, type Tier } from './types.js';

const MAX_BODY_BYTES = 25 * 1024 * 1024;
const TIERS: readonly Tier[] = ['cheap', 'mid', 'smart'];

function header(req: http.IncomingMessage, name: string): string | undefined {
  const value = req.headers[name];
  return (Array.isArray(value) ? value[0] : value)?.trim().toLowerCase();
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new RequestError(413, 'request body is too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function sendJson(res: http.ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', ...headers });
  res.end(JSON.stringify(body));
}

function sendError(res: http.ServerResponse, err: unknown): void {
  let status = 500;
  let type = 'router_error';
  if (err instanceof RequestError) {
    status = err.status;
    type = 'invalid_request_error';
  } else if (err instanceof UpstreamError) {
    // 0/1xx-3xx never reach here; anything odd from upstream becomes a 502.
    status = err.status >= 400 && err.status < 600 ? err.status : 502;
    type = `upstream_error_${err.provider}`;
  }
  const message = err instanceof Error ? err.message : String(err);
  if (status >= 500 && !(err instanceof UpstreamError)) console.error('[atuo] request failed:', err);
  if (res.headersSent) {
    res.end();
    return;
  }
  sendJson(res, status, { error: { message, type, code: status } });
}

function metaHeaders(meta: ChatMeta): Record<string, string> {
  return {
    'x-atuo-provider': meta.provider,
    'x-atuo-model': meta.model,
    'x-atuo-tier': meta.tier ?? 'explicit',
    'x-atuo-cache': meta.cache,
    'x-atuo-saved-tokens': String(meta.savedTokens),
    // Header values must be Latin-1; route reasons are plain ASCII already.
    'x-atuo-route': meta.route.replace(/[^\x20-\x7e]/g, '?'),
  };
}

function chatOptions(req: http.IncomingMessage, signal: AbortSignal): ChatOptions {
  const opts: ChatOptions = { signal };
  if (header(req, 'x-atuo-cache') === 'off') opts.cache = false;
  const compression = header(req, 'x-atuo-compress') as CompressionMode | undefined;
  if (compression) {
    if (!COMPRESSION_MODES.includes(compression)) {
      throw new RequestError(400, `x-atuo-compress must be one of ${COMPRESSION_MODES.join(', ')}`);
    }
    opts.compression = compression;
  }
  const tier = header(req, 'x-atuo-tier') as Tier | undefined;
  if (tier) {
    if (!TIERS.includes(tier)) throw new RequestError(400, `x-atuo-tier must be one of ${TIERS.join(', ')}`);
    opts.tier = tier;
  }
  return opts;
}

async function handleChat(app: RouterApp, req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  let body: ChatRequest;
  try {
    body = JSON.parse(await readBody(req)) as ChatRequest;
  } catch (err) {
    throw err instanceof RequestError ? err : new RequestError(400, 'request body is not valid JSON');
  }

  // Stop paying for output nobody is reading when the client disconnects.
  const abort = new AbortController();
  res.on('close', () => {
    if (!res.writableEnded) abort.abort();
  });

  const result = await app.chat(body, chatOptions(req, abort.signal));
  if (!result.chunks) {
    sendJson(res, 200, result.completion, metaHeaders(result.meta));
    return;
  }

  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
    ...metaHeaders(result.meta),
  });
  const wantsUsage = body.stream_options?.include_usage === true;
  try {
    for await (const chunk of result.chunks) {
      // The usage-only chunk is always produced internally; clients only get it on request.
      if (!chunk.choices.length && !wantsUsage) continue;
      res.write(`data: ${JSON.stringify(chunk)}\n\n`);
    }
    res.write('data: [DONE]\n\n');
  } catch (err) {
    if (!abort.signal.aborted) {
      const message = err instanceof Error ? err.message : String(err);
      console.error('[atuo] stream failed mid-response:', message);
      res.write(`data: ${JSON.stringify({ error: { message, type: 'upstream_error' } })}\n\n`);
    }
  }
  res.end();
}

export function createServer(app: RouterApp): http.Server {
  const { routerApiKey, corsOrigin } = app.cfg;

  const authorized = (req: http.IncomingMessage): boolean => {
    if (!routerApiKey) return true;
    const sent = /^Bearer\s+(.+)$/i.exec(req.headers.authorization ?? '')?.[1] ?? '';
    const a = crypto.createHash('sha256').update(sent).digest();
    const b = crypto.createHash('sha256').update(routerApiKey).digest();
    return crypto.timingSafeEqual(a, b);
  };

  return http.createServer((req, res) => {
    void (async () => {
      const url = new URL(req.url ?? '/', 'http://localhost');
      // Accept both `/v1/...` and bare paths: clients disagree on who adds the prefix.
      const route = url.pathname.replace(/^\/v1(?=\/)/, '').replace(/\/+$/, '') || '/';

      if (corsOrigin) {
        res.setHeader('access-control-allow-origin', corsOrigin);
        res.setHeader('access-control-allow-headers', 'authorization, content-type, x-atuo-cache, x-atuo-compress, x-atuo-tier');
        res.setHeader('access-control-expose-headers', 'x-atuo-provider, x-atuo-model, x-atuo-tier, x-atuo-cache, x-atuo-saved-tokens, x-atuo-route');
        if (req.method === 'OPTIONS') {
          res.writeHead(204).end();
          return;
        }
      }

      if (req.method === 'GET' && route === '/') {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }).end(DASHBOARD_HTML);
        return;
      }
      if (req.method === 'GET' && route === '/health') {
        sendJson(res, 200, { ok: true });
        return;
      }
      if (!authorized(req)) {
        throw new RequestError(401, 'Missing or wrong router key. Send `Authorization: Bearer <ROUTER_API_KEY>`.');
      }
      if (req.method === 'POST' && route === '/chat/completions') {
        await handleChat(app, req, res);
      } else if (req.method === 'GET' && route === '/models') {
        sendJson(res, 200, { object: 'list', data: app.models() });
      } else if (req.method === 'GET' && route === '/stats') {
        sendJson(res, 200, app.stats());
      } else if (req.method === 'POST' && route === '/cache/clear') {
        app.cache.clear();
        sendJson(res, 200, { ok: true });
      } else {
        throw new RequestError(404, `No route for ${req.method} ${url.pathname}`);
      }
    })().catch((err) => sendError(res, err));
  });
}
