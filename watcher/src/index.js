// hello-rss の「時計」。Cloudflare の Cron Trigger で、GitHub Actions の作業を決まった時刻に呼び出す。
// フィードを配る Worker（ルートの wrangler.toml）に同居している。
// GitHub の定期実行は数時間遅れたり飛ばされたりするので使わない。記憶（KV など）は持たない。
//
//   */15 * * * *  ハロプロ・JPROOM は、公式のJSONの ETag（中身のハッシュ）が前回の作り直しと違えば、ハロショは、フィードの Last-Modified が直近17分以内なら、rss.yml を呼ぶ
//   0 20 * * *    05:00 JST  rss.yml（今日の予定）・tver.yml（放送後の TVer のリンクを探す）・episodes.yml（ポッドキャスト・YouTube のエピソードを結びつける）
//   0 9 * * *     18:00 JST  hpwatch.yml（スケジュールの Discord 通知）と profiles.yml（プロフィール）と tver.yml と episodes.yml を別々に呼ぶ
//
// 呼ぶ側が二重に呼ばれても困らない（どの作業も、新しい分だけを処理する）。呼び出しに失敗したら、そのたびに Discord に知らせる。

import { customFeed } from "./custom.js";
import { importRoutes } from "./import.js";
import { reportRoute } from "./report.js";

// 公式の更新を見るページ（HEAD の Last-Modified）。ハロプロ・JPROOM・アップフロントは同じ版で作り直される（/schedule/）。
// ハロショ（WordPress）は別のサイトなので、別に見る（/feed。記事が載ると更新される）。ファンクラブのページには更新の印が無いので見られない
const WATCH = {
  helloshop: "https://www.helloshop.info/feed",
};
// ハロプロ・JPROOM の公式は、15分ごとに必ず作り直される（版 versionDir も更新時刻も毎回変わる）ので、時刻では「変わった」が分からない。
// JSON の ETag は中身のハッシュなので、中身が同じなら、作り直されても変わらない。rss.py が、作り直しで使った ETag を data/watch.json に公開している。
// ここでは、公式の今の ETag（HEAD）と比べて、違うときだけ rss.yml を呼ぶ（Worker は何も覚えず、公開したデータが記憶になる）
const OFFICIAL = {
  hp: "https://helloproject.com",
  jproom: "https://www.jp-r.co.jp",
};
const UA = "Mozilla/5.0 (hp-watch; cloudflare cron)";
const DETECT = "*/15 * * * *";
const WINDOW_MS = 17 * 60 * 1000; // 15分おきの実行 ＋ 少しの余裕
const FIXED = {
  "0 20 * * *": ["rss.yml", "tver.yml", "episodes.yml"],
  "0 9 * * *": ["hpwatch.yml", "profiles.yml", "tver.yml", "episodes.yml"],
};

// ページが最後に更新された時刻。直近 WINDOW_MS 以内なら Date、そうでなければ null
export async function builtRecently(url, now) {
  const r = await fetch(url, { method: "HEAD", headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  const lm = r.headers.get("last-modified");
  if (!lm) return null;
  const t = new Date(lm);
  const age = now.getTime() - t.getTime();
  return age >= -60_000 && age <= WINDOW_MS ? t : null;
}

async function readWatch(env) {
  if (!env.ASSETS) return null;
  const r = await env.ASSETS.fetch(new Request("https://assets.invalid/data/watch.json"));
  return r.ok ? r.json() : null;
}

// 公式のお知らせ・スケジュール（今年）の ETag が、前回の作り直しで使ったものと違えば、その理由。同じなら null
export async function officialChanged(env, base, now) {
  const page = (await (await fetch(`${base}/schedule/`, { headers: { "User-Agent": UA } })).text()).replace(/&quot;/g, '"');
  const m = page.match(/"versionDir":\[0,"([^"]+)"\]/);
  if (!m) throw new Error(`${base}: versionDir が見つからない`);
  const known = ((await readWatch(env)) || {})[base];
  if (!known) return "data/watch.json に記録が無い";
  const year = new Date(now.getTime() + 9 * 3600e3).getUTCFullYear();
  let seen = 0;
  for (const f of [`${year}_news_list.json`, `${year}_schedules.json`]) {
    const r = await fetch(`${base}/json/${m[1]}/${f}`, { method: "HEAD", headers: { "User-Agent": UA } });
    if (!r.ok) continue; // 年が変わった直後などで、まだ無いことがある
    seen++;
    if (known[f] !== r.headers.get("etag")) return `${f} が変わった`;
  }
  if (!seen) throw new Error(`${base}: ${year} のJSONが取れない`);
  return null;
}

export async function dispatch(env, workflow, ref = "main") {
  const url = `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`;
  let last;
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "hello-rss",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref }),
      });
      if (r.status === 204 || r.status === 200) return;
      last = `${workflow}: ${r.status} ${(await r.text()).slice(0, 200)}`;
      if (r.status < 500) break; // 401・404・422 などは、やり直しても同じ
    } catch (e) {
      last = `${workflow}: ${e && e.message || e}`;
    }
    await new Promise((res) => setTimeout(res, 1000 * (i + 1)));
  }
  throw new Error(last);
}

async function notifyFailure(env, text) {
  // 失敗したら毎回知らせる。呼び出しは1日に十数回（更新の検知と、5:00・18:00）しか無いので、続いても数が限られる。
  // 時刻で間引くと、間引かれた回の失敗が誰にも知られない
  if (!env.DISCORD_WEBHOOK_URL) return;
  await fetch(env.DISCORD_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", "User-Agent": UA },
    body: JSON.stringify({ username: "hello-rss", content: `⚠️ GitHub の作業を呼べませんでした\n${text}`.slice(0, 1900) }),
  }).catch(() => {});
}

export async function run(cron, env, now) {
  const jobs = new Map(); // 作業 → 理由
  for (const w of FIXED[cron] || []) jobs.set(w, cron);
  const errors = [];
  if (cron === DETECT) {
    for (const [site, base] of Object.entries(OFFICIAL)) {
      try {
        const why = await officialChanged(env, base, now);
        if (why) jobs.set("rss.yml", `${site} ${why}`);
      } catch (e) {
        errors.push(`${site}: ${e && e.message || e}`); // 公式側の一時的な失敗。呼び出しの失敗ではないので Discord には出さない
      }
    }
    for (const [site, url] of Object.entries(WATCH)) {
      try {
        const t = await builtRecently(url, now);
        if (t) jobs.set("rss.yml", `${site} 更新 ${t.toISOString()}`);
      } catch (e) {
        errors.push(`${site}: ${e && e.message || e}`); // 公式側の一時的な失敗。呼び出しの失敗ではないので Discord には出さない
      }
    }
  }
  const failed = [];
  for (const [w, why] of jobs) {
    try {
      await dispatch(env, w);
    } catch (e) {
      failed.push(String(e.message || e));
    }
  }
  const summary = { cron, at: now.toISOString(), dispatched: [...jobs].map(([w, why]) => `${w} (${why})`), failed, errors };
  console.log(JSON.stringify(summary));
  if (failed.length) await notifyFailure(env, failed.join("\n"));
  return summary;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(run(controller.cron, env, new Date(controller.scheduledTime)));
  },
  // フィード（public/）に無いパスだけが、ここに来る（あるファイルは、Cloudflare が直接返す）。
  // /custom.xml は選択式フィード（custom.js）。それ以外の無いURLはトップページ（フィードの一覧）へ移す。
  // 302 にしておく（打ち間違いのURLを、リーダーに永久に覚えさせない）
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/custom.xml") return customFeed(req, env);
    // 「ご連絡」のフォームの受け口（このサイトについて、のページ）
    const rep = await reportRoute(req, env);
    if (rep) return rep;
    // 放送の情報を手で入れるページ（鍵つき）。保存のあと、フィードを作り直す（rss.yml）
    const imp = await importRoutes(req, env, { now: new Date(), dispatch: (w) => dispatch(env, w) });
    if (imp) return imp;
    return Response.redirect(new URL("/", req.url).toString(), 302);
  },
};
