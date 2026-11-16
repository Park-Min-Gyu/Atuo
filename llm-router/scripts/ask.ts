// Quick command-line check:  npm run ask -- "질문" [--model smart|mid|cheap|auto]
try {
  process.loadEnvFile('.env');
} catch {
  // optional
}

const args = process.argv.slice(2);
const modelFlag = args.indexOf('--model');
const model = modelFlag >= 0 ? args.splice(modelFlag, 2)[1] : 'auto';
const question = args.join(' ').trim();
if (!question) {
  console.error('사용법: npm run ask -- "질문" [--model auto|cheap|mid|smart]');
  process.exit(1);
}

const base = `http://127.0.0.1:${process.env.PORT ?? 8787}`;
const res = await fetch(`${base}/v1/chat/completions`, {
  method: 'POST',
  headers: {
    'content-type': 'application/json',
    ...(process.env.ROUTER_API_KEY ? { authorization: `Bearer ${process.env.ROUTER_API_KEY}` } : {}),
  },
  body: JSON.stringify({
    model,
    stream: true,
    stream_options: { include_usage: true },
    messages: [{ role: 'user', content: question }],
  }),
}).catch(() => {
  console.error(`라우터에 연결할 수 없습니다 (${base}). 다른 터미널에서 npm start 를 먼저 실행하세요.`);
  process.exit(1);
});

if (!res.ok || !res.body) {
  console.error(`오류 ${res.status}: ${await res.text()}`);
  process.exit(1);
}

console.error(
  `→ ${res.headers.get('x-atuo-provider')} / ${res.headers.get('x-atuo-model')} · tier ${res.headers.get('x-atuo-tier')} · cache ${res.headers.get('x-atuo-cache')}\n  ${res.headers.get('x-atuo-route')}\n`,
);

const decoder = new TextDecoder();
let buffer = '';
let usage: { prompt_tokens: number; completion_tokens: number } | undefined;
for await (const bytes of res.body) {
  buffer += decoder.decode(bytes, { stream: true });
  let newline: number;
  while ((newline = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
    const event = JSON.parse(line.slice(6));
    if (event.error) console.error(`\n오류: ${event.error.message}`);
    if (event.usage) usage = event.usage;
    process.stdout.write(event.choices?.[0]?.delta?.content ?? '');
  }
}
console.error(usage ? `\n\n(입력 ${usage.prompt_tokens} · 출력 ${usage.completion_tokens} 토큰)` : '\n');
