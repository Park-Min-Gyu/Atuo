import { RouterApp } from './app.js';
import { loadConfig, type Config } from './config.js';
import { createServer } from './server.js';

try {
  process.loadEnvFile('.env');
} catch {
  // no .env file — configuration comes from the real environment
}

function readConfig(): Config {
  try {
    return loadConfig();
  } catch (err) {
    console.error(`설정 오류: ${(err as Error).message}`);
    process.exit(1);
  }
}
const cfg = readConfig();

const app = new RouterApp(cfg);
if (!app.hasProviders) {
  console.error(
    'API 키가 없습니다. .env.example 을 .env 로 복사한 뒤 GEMINI_API_KEY (와 선택적으로 ANTHROPIC_API_KEY) 를 넣어 주세요.',
  );
  process.exit(1);
}

/** Warns early when a configured Gemini model id does not exist for this key. */
async function checkGeminiModels(): Promise<void> {
  if (!cfg.gemini.apiKey) return;
  try {
    const res = await fetch(`${cfg.gemini.baseUrl}/models`, {
      headers: { authorization: `Bearer ${cfg.gemini.apiKey}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      console.warn(`[atuo] Gemini 키 확인 실패 (HTTP ${res.status}). GEMINI_API_KEY 를 확인하세요.`);
      return;
    }
    const body = (await res.json()) as { data?: Array<{ id: string }> };
    const ids = new Set((body.data ?? []).map((m) => m.id.replace(/^models\//, '')));
    for (const model of new Set([cfg.gemini.cheapModel, cfg.gemini.midModel])) {
      if (ids.size && !ids.has(model)) {
        const similar = [...ids].filter((id) => id.startsWith('gemini-') && /flash|pro/.test(id)).slice(0, 12);
        console.warn(`[atuo] Gemini 모델 "${model}" 을 찾을 수 없습니다. 사용 가능한 모델 예: ${similar.join(', ')}`);
      }
    }
  } catch {
    // offline at startup is not fatal; real requests will report their own errors
  }
}

const server = createServer(app);
server.listen(cfg.port, cfg.host, () => {
  const base = `http://${cfg.host === '0.0.0.0' ? 'localhost' : cfg.host}:${cfg.port}`;
  console.log(`Atuo Router 실행 중 → ${base}`);
  console.log(`  OpenAI 호환 주소 : ${base}/v1`);
  console.log(`  대시보드          : ${base}/`);
  console.log(`  Gemini           : ${cfg.gemini.apiKey ? `${cfg.gemini.cheapModel} (cheap) / ${cfg.gemini.midModel} (mid)` : '꺼짐 (GEMINI_API_KEY 없음)'}`);
  console.log(`  Claude           : ${cfg.claude.enabled ? `${cfg.claude.model} (smart)` : '꺼짐 (ANTHROPIC_API_KEY 없음) — smart 요청은 Gemini mid 로 갑니다'}`);
  console.log(`  압축 ${cfg.compression.mode} · 캐시 ${cfg.cache.enabled ? '켜짐' : '꺼짐'} · 라우터 키 ${cfg.routerApiKey ? '사용' : '없음'}`);
  if (!cfg.routerApiKey && cfg.host !== '127.0.0.1' && cfg.host !== 'localhost') {
    console.warn('[atuo] 경고: 외부에서 접속 가능한 주소인데 ROUTER_API_KEY 가 없습니다. 누구나 내 토큰을 쓸 수 있습니다.');
  }
  void checkGeminiModels();
});

function shutdown(): void {
  app.close();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
