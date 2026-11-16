// Single-file status page served at `/`. It only reads `/stats`.
export const DASHBOARD_HTML = /* html */ `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atuo Router</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#16181d; --muted:#667085; --line:#e4e7ec; --accent:#2f6fed; --good:#12805c; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --card:#181b21; --text:#e8eaee; --muted:#98a2b3; --line:#2a2e37; --accent:#7aa5ff; --good:#4cc79a; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--text);
         font: 15px/1.5 system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }
  main { max-width: 920px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 14px; margin: 28px 0 10px; color: var(--muted); font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap:12px; margin-top:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card .label { font-size:12px; color:var(--muted); }
  .card .value { font-size:24px; font-weight:650; font-variant-numeric: tabular-nums; margin-top:2px; }
  .card .hint { font-size:12px; color:var(--muted); margin-top:2px; }
  .good { color: var(--good); }
  .table { background:var(--card); border:1px solid var(--line); border-radius:10px; overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-variant-numeric: tabular-nums; }
  th, td { padding:9px 14px; text-align:right; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { font-size:12px; color:var(--muted); font-weight:600; }
  th:first-child, td:first-child { text-align:left; }
  tr:last-child td { border-bottom:0; }
  .pill { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; border:1px solid var(--line); color:var(--muted); margin-right:6px; }
  .pill.on { color: var(--good); border-color: var(--good); }
  #error { color:#d92d20; margin-top:16px; }
  input { font: inherit; padding:6px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--text); }
  button { font: inherit; padding:6px 12px; border-radius:8px; border:1px solid var(--accent); background:var(--accent); color:#fff; cursor:pointer; }
</style>
</head>
<body>
<main>
  <h1>Atuo Router</h1>
  <div class="sub" id="status">불러오는 중…</div>
  <div id="error"></div>
  <div id="login" hidden>
    <p class="sub">이 라우터는 키로 보호되어 있습니다. ROUTER_API_KEY 값을 입력하세요.</p>
    <input id="key" type="password" placeholder="ROUTER_API_KEY"> <button id="save">확인</button>
  </div>
  <div id="content" hidden>
    <div class="grid" id="cards"></div>
    <h2>오늘 모델별 사용량</h2>
    <div class="table"><table id="models"></table></div>
    <h2>최근 일별 기록</h2>
    <div class="table"><table id="days"></table></div>
    <p class="sub">비용은 설정된 단가표로 계산한 추정치입니다. 실제 청구액은 각 제공사 콘솔에서 확인하세요.</p>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const num = (n) => Math.round(n || 0).toLocaleString('ko-KR');
const usd = (n) => '$' + (n || 0).toFixed(n >= 1 ? 2 : 4);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let key = '';
try { key = localStorage.getItem('atuoKey') || ''; } catch {}

function card(label, value, hint, good) {
  return '<div class="card"><div class="label">' + esc(label) + '</div><div class="value' + (good ? ' good' : '') + '">' +
    esc(value) + '</div><div class="hint">' + esc(hint || '') + '</div></div>';
}

function dayCost(day) {
  return Object.values(day.models).reduce((sum, m) => sum + m.costUsd, 0);
}

function render(s) {
  const day = s.usage.days[s.usage.today];
  const models = Object.entries(day.models);
  const spent = dayCost(day);
  const saved = Math.max(0, day.premiumOnlyUsd - spent);
  const requests = models.reduce((sum, [, m]) => sum + m.requests, 0) + day.cacheHits;
  const g = s.providers.gemini, c = s.providers.claude;
  $('status').innerHTML =
    '<span class="pill ' + (g.enabled ? 'on' : '') + '">Gemini ' + (g.enabled ? esc(g.cheapModel + ' / ' + g.midModel) : '꺼짐') + '</span>' +
    '<span class="pill ' + (c.enabled ? 'on' : '') + '">Claude ' + (c.enabled ? esc(c.model) : '꺼짐') + '</span>' +
    '<span class="pill">압축 ' + esc(s.compression) + '</span>' +
    '<span class="pill">캐시 ' + (s.cache.enabled ? s.cache.entries + '건' : '꺼짐') + '</span>';

  $('cards').innerHTML =
    card('오늘 요청', num(requests), 'cheap ' + day.tiers.cheap + ' · mid ' + day.tiers.mid + ' · smart ' + day.tiers.smart) +
    card('오늘 추정 비용', usd(spent), '전부 ' + c.model + '였다면 ' + usd(day.premiumOnlyUsd)) +
    card('라우팅·캐시로 아낀 금액', usd(saved), '추정치', true) +
    card('캐시 적중', num(day.cacheHits) + '회', num(day.cacheSavedTokens) + ' 토큰 절약', true) +
    card('압축으로 줄인 토큰', num(day.compressionSavedTokens + day.summarySavedTokens), '요약 ' + num(day.summarySavedTokens), true) +
    card('Claude 오늘 사용', num(c.tokensToday) + ' 토큰', c.dailyTokenBudget ? '한도 ' + num(c.dailyTokenBudget) : '한도 없음');

  $('models').innerHTML = '<tr><th>모델</th><th>요청</th><th>입력</th><th>캐시된 입력</th><th>출력</th><th>추정 비용</th></tr>' +
    (models.length ? models.map(([id, m]) => '<tr><td>' + esc(id) + '</td><td>' + num(m.requests) + '</td><td>' + num(m.inputTokens) +
      '</td><td>' + num(m.cachedInputTokens) + '</td><td>' + num(m.outputTokens) + '</td><td>' + usd(m.costUsd) + '</td></tr>').join('')
      : '<tr><td colspan="6" class="sub">아직 요청이 없습니다.</td></tr>');

  const days = Object.entries(s.usage.days).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 14);
  $('days').innerHTML = '<tr><th>날짜</th><th>요청</th><th>캐시 적중</th><th>폴백</th><th>추정 비용</th><th>아낀 금액</th></tr>' +
    days.map(([date, d]) => {
      const cost = dayCost(d);
      const count = Object.values(d.models).reduce((sum, m) => sum + m.requests, 0) + d.cacheHits;
      return '<tr><td>' + esc(date) + '</td><td>' + num(count) + '</td><td>' + num(d.cacheHits) + '</td><td>' + num(d.fallbacks) +
        '</td><td>' + usd(cost) + '</td><td>' + usd(Math.max(0, d.premiumOnlyUsd - cost)) + '</td></tr>';
    }).join('');
}

async function load() {
  try {
    const res = await fetch('/stats', { headers: key ? { authorization: 'Bearer ' + key } : {} });
    if (res.status === 401) { $('login').hidden = false; $('content').hidden = true; $('status').textContent = ''; return; }
    if (!res.ok) throw new Error('HTTP ' + res.status);
    render(await res.json());
    $('login').hidden = true; $('content').hidden = false; $('error').textContent = '';
  } catch (err) {
    $('error').textContent = '통계를 불러오지 못했습니다: ' + err.message;
  }
}
$('save').onclick = () => { key = $('key').value.trim(); try { localStorage.setItem('atuoKey', key); } catch {} load(); };
load();
setInterval(load, 5000);
</script>
</body>
</html>
`;
