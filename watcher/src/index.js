// hello-rss-watch: 公式サイト（ハロプロ・JPROOM）の更新を15分おきに見て、いつ更新されたかを記録する（様子見用）
//
// 公式の /schedule/ ページは、サイトを作り直すたびに Last-Modified と ETag が変わる。
// ヘッダーだけ（HEAD）で更新を見分け、変わったときだけ「版の名前」と、お知らせ・スケジュールのJSONの
// サイズ／ETag を記録する。版の名前（2611191300-… ＝ 日本時間 2026-11-19 13:00）は、サイトを作り直した時刻でもある。
// JSON本体は取らない（無料プランの CPU 時間は1回10msまでなので、4MBのJSONは読まない）。
//
// 記録は KV に持つ:
//   state:{site} … 前回の ETag など。毎回 lastPoll を更新する
//   log          … 更新のたびの記録（新しい順ではなく古い順、最大 LOG_MAX 件）
// 見るとき: GET /{LOG_PATH}  （?text で読みやすい形）

const SITES = { hp: "https://helloproject.com", jproom: "https://www.jp-r.co.jp" };
const UA = "Mozilla/5.0 (hp-watch; update observer)";
const LOG_MAX = 500;
const JST_MS = 9 * 3600 * 1000;
const VERSION_RE = /versionDir(?:&quot;|")\s*:\s*\[0,\s*(?:&quot;|")([A-Za-z0-9-]+)/;

// 2026-11-19T13:00:13+09:00（秒まで）
const toJst = (d) => new Date(d.getTime() + JST_MS).toISOString().replace(/\.\d+Z$/, "+09:00");

async function head(url) {
  const r = await fetch(url, { method: "HEAD", headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return {
    etag: r.headers.get("etag"),
    lastModified: r.headers.get("last-modified"),
    bytes: Number(r.headers.get("content-length")) || null,
  };
}

// 版の名前はページの先頭付近（約13KB目）にある。見つかったら読むのをやめる
async function findVersion(base) {
  const r = await fetch(`${base}/schedule/`, { headers: { "User-Agent": UA } });
  if (!r.ok || !r.body) throw new Error(`${r.status} ${base}/schedule/`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const m = VERSION_RE.exec(buf);
      if (m) return m[1];
      if (buf.length > 300000) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  return null;
}

function part(cur, prev) {
  if (!cur) return null;
  return {
    bytes: cur.bytes,
    etag: cur.etag,
    changed: prev ? prev.etag !== cur.etag : null,
    delta: prev && prev.bytes != null && cur.bytes != null ? cur.bytes - prev.bytes : null,
  };
}

// 1サイトを見る。更新があれば記録（log に足す1件）を返す。無ければ null
export async function pollSite(env, site, base, now) {
  const key = `state:${site}`;
  const prev = JSON.parse((await env.WATCH.get(key)) || "null");
  const polledAt = toJst(now);
  try {
    const page = await head(`${base}/schedule/`);
    if (prev && prev.pageEtag === page.etag) {
      await env.WATCH.put(key, JSON.stringify({ ...prev, lastPoll: polledAt, lastError: null }));
      return null;
    }
    const version = await findVersion(base);
    const year = new Date(now.getTime() + JST_MS).getUTCFullYear();
    const [news, sched] = await Promise.all([
      version ? head(`${base}/json/${version}/${year}_news_list.json`).catch(() => null) : null,
      version ? head(`${base}/json/${version}/${year}_schedules.json`).catch(() => null) : null,
    ]);
    const entry = {
      site,
      polledAt,
      versionTime: page.lastModified ? toJst(new Date(page.lastModified)) : null,
      version,
      first: !prev,
      news: part(news, prev && prev.news),
      schedule: part(sched, prev && prev.schedule),
    };
    await env.WATCH.put(key, JSON.stringify({
      pageEtag: page.etag, version, news: news && { bytes: news.bytes, etag: news.etag },
      schedule: sched && { bytes: sched.bytes, etag: sched.etag }, lastPoll: polledAt, lastError: null,
    }));
    return entry;
  } catch (e) {
    const message = String(e && e.message || e);
    await env.WATCH.put(key, JSON.stringify({ ...(prev || {}), lastPoll: polledAt, lastError: message }));
    // 同じエラーが続くときは記録しない
    return prev && prev.lastError === message ? null : { site, polledAt, error: message };
  }
}

export async function poll(env, now = new Date()) {
  const entries = [];
  for (const [site, base] of Object.entries(SITES)) {
    const e = await pollSite(env, site, base, now);
    if (e) entries.push(e);
  }
  if (entries.length) {
    const log = JSON.parse((await env.WATCH.get("log")) || "[]");
    await env.WATCH.put("log", JSON.stringify(log.concat(entries).slice(-LOG_MAX)));
  }
  return entries;
}

const kb = (n) => (n == null ? "?" : `${n >= 0 ? "+" : ""}${(n / 1024).toFixed(1)}KB`);

function lineOf(e) {
  if (e.error) return `${e.polledAt} ${e.site} エラー ${e.error}`;
  const f = (name, p) => (p ? `${name}${p.changed === null ? "(初回)" : p.changed ? ` ${kb(p.delta)}` : " 変化なし"}` : `${name}?`);
  return `${e.versionTime} ${e.site} 版=${e.version}  ${f("お知らせ", e.news)}  ${f("スケジュール", e.schedule)}  （検知 ${e.polledAt}）`;
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(poll(env));
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    if (!env.LOG_PATH || url.pathname !== `/${env.LOG_PATH}`) return new Response("not found", { status: 404 });
    const log = JSON.parse((await env.WATCH.get("log")) || "[]");
    const state = {};
    for (const site of Object.keys(SITES)) state[site] = JSON.parse((await env.WATCH.get(`state:${site}`)) || "null");
    if (url.searchParams.has("text")) {
      const head = Object.entries(state).map(([s, v]) => `${s}: 最後に見た ${v && v.lastPoll}  版=${v && v.version}${v && v.lastError ? "  エラー=" + v.lastError : ""}`);
      return new Response([...head, "", ...log.map(lineOf).reverse()].join("\n") + "\n", { headers: { "content-type": "text/plain; charset=utf-8" } });
    }
    return new Response(JSON.stringify({ state, log }, null, 1), { headers: { "content-type": "application/json; charset=utf-8" } });
  },
};
