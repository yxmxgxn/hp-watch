// hello-rss の「時計」。Cloudflare の Cron Trigger で、GitHub Actions の作業を決まった時刻に呼び出す。
// フィードを配る Worker（ルートの wrangler.toml）に同居している。
// GitHub の定期実行は数時間遅れたり飛ばされたりするので使わない。記憶（KV など）は持たない。
//
//   */15 * * * *  公式の更新を見るページ（WATCH）の Last-Modified が直近17分以内なら（＝サイトが更新されたら）rss.yml を呼ぶ
//   0 20 * * *    05:00 JST  rss.yml（今日の予定）
//   0 9 * * *     18:00 JST  hpwatch.yml（スケジュールの Discord 通知）と profiles.yml（プロフィール）を別々に呼ぶ
//
// 呼ぶ側が二重に呼ばれても困らない（どの作業も、新しい分だけを処理する）。呼び出しに失敗したら、そのたびに Discord に知らせる。

import { customFeed } from "./custom.js";

// 公式の更新を見るページ（HEAD の Last-Modified）。ハロプロ・JPROOM・アップフロントは同じ版で作り直される（/schedule/）。
// ハロショ・ジャストプロ（WordPress）は別のサイトなので、別に見る（/feed。記事が載ると更新される）。田中れいな・鈴木愛理・小片リサの公式サイトも、別（お知らせのページ・feed）。小林梓・夏焼雅のサイトには更新の印が無いので見られない（rss.yml が動いたときに見る）。ファンクラブのページには更新の印が無いので見られない
const WATCH = {
  hp: "https://helloproject.com/schedule/",
  jproom: "https://www.jp-r.co.jp/schedule/",
  helloshop: "https://www.helloshop.info/feed",
  justpro: "https://www.just-pro.jp/feed/",
  reina: "https://tanaka-reina.com/news/",
  airi: "https://www.airisuzuki-officialweb.com/news/index.html",
  ogata: "https://risaogata.com/feed/",
};
const UA = "Mozilla/5.0 (hp-watch; cloudflare cron)";
const DETECT = "*/15 * * * *";
const WINDOW_MS = 17 * 60 * 1000; // 15分おきの実行 ＋ 少しの余裕
const FIXED = {
  "0 20 * * *": ["rss.yml"],
  "0 9 * * *": ["hpwatch.yml", "profiles.yml"],
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
    return Response.redirect(new URL("/", req.url).toString(), 302);
  },
};
