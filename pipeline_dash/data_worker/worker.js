// 帶案儀表板的資料口（2026-10-20）：pipeline.step1ne.com 的 Worker 在另一個 Cloudflare 帳號，
// 每次有人打開頁面時來這裡拿 D1 pipeline_weekly 的最新資料。這樣 WSL2 只要寫 D1，不用部署網頁。
// 只回給帶正確 X-Pipe-Key 的請求（secret PIPE_KEY，兩邊 Worker 同一把）；資料含人選全名，不准公開。
export default {
  async fetch(request, env) {
    if (!env.PIPE_KEY || request.headers.get('x-pipe-key') !== env.PIPE_KEY) {
      return new Response('forbidden', { status: 403 });
    }
    const { results } = await env.DB.prepare(
      'SELECT data FROM pipeline_weekly ORDER BY wk DESC LIMIT 26').all();
    const weeks = [];
    for (const r of results) {
      let s = r.data || '';
      if (s.startsWith('gz:')) {
        const bin = Uint8Array.from(atob(s.slice(3)), (ch) => ch.charCodeAt(0));
        const stream = new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'));
        s = await new Response(stream).text();
      }
      weeks.push(JSON.parse(s));
    }
    return new Response(JSON.stringify({ weeks }), {
      headers: { 'content-type': 'application/json;charset=utf-8', 'cache-control': 'no-store' } });
  }
};
