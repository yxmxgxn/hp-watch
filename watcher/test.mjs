// 手元の確認: NODE_USE_ENV_PROXY=1 node watcher/test.mjs
// 公式サイトへの HEAD は本物、GitHub と Discord への呼び出しは偽物
import watcher, { run, builtRecently } from "./src/index.js";
import { parseWpFeed } from "./src/custom.js";

const realFetch = globalThis.fetch;
let calls = [];
let githubStatus = 204;
globalThis.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (u.startsWith("https://api.github.com/") || u.startsWith("https://discord.test/")) {
    calls.push({ url: u, opts });
    return new Response(githubStatus === 204 ? null : "boom", { status: u.includes("discord") ? 204 : githubStatus });
  }
  return realFetch(url, opts);
};
const env = { GH_REPO: "o/r", GH_TOKEN: "tok", DISCORD_WEBHOOK_URL: "https://discord.test/hook" };
const assert = (c, m) => { if (!c) { console.error("NG:", m); process.exit(1); } console.log("ok:", m); };
const gh = () => calls.filter((c) => c.url.includes("api.github.com")).map((c) => c.url.split("/workflows/")[1].split("/")[0]);

// 本物のサイトの Last-Modified を基準にする
const head = await realFetch("https://helloproject.com/schedule/", { method: "HEAD" });
const lm = new Date(head.headers.get("last-modified"));
console.log("公式の最後の更新:", lm.toISOString());
const at = (min) => new Date(lm.getTime() + min * 60000);

assert((await builtRecently("https://helloproject.com", at(5)))?.getTime() === lm.getTime(), "更新の5分後 → 直近の更新として検知");
assert((await builtRecently("https://helloproject.com", at(90))) === null, "更新の90分後 → 検知しない");

calls = []; let s = await run("*/15 * * * *", env, at(5));
assert(JSON.stringify(gh()) === '["rss.yml"]', "更新の直後（2サイトとも更新）でも rss.yml を1回だけ呼ぶ");
const c = calls[0];
assert(c.url === "https://api.github.com/repos/o/r/actions/workflows/rss.yml/dispatches", "呼び出し先のURL");
assert(c.opts.method === "POST" && c.opts.headers.Authorization === "Bearer tok" && JSON.parse(c.opts.body).ref === "main", "POST・トークン・ref=main");

calls = []; await run("*/15 * * * *", env, at(90));
assert(gh().length === 0, "更新から90分後は何も呼ばない");

calls = []; await run("0 9 * * *", env, at(90));
assert(JSON.stringify(gh()) === '["hpwatch.yml","profiles.yml"]', "18:00 JST は hpwatch.yml と profiles.yml を別々に呼ぶ");

calls = []; await run("0 20 * * *", env, at(90));
assert(JSON.stringify(gh()) === '["rss.yml"]', "05:00 JST は rss.yml");

calls = []; await run("0 3 * * *", env, at(5));
assert(calls.length === 0, "知らない時刻の cron は何もしない");

// 失敗: 401 はやり直さず、Discord に知らせる（毎時の最初の15分だけ）
githubStatus = 401; calls = [];
s = await run("0 9 * * *", env, new Date("2026-11-19T09:00:00Z"));
assert(s.failed.length === 2 && gh().length === 2, "401 はやり直さない（2作業×1回）");
assert(calls.filter((x) => x.url.includes("discord")).length === 1, "失敗を Discord に1回知らせる");
calls = []; await run("*/15 * * * *", env, at(5)); // 更新の検知で呼ぶ回。時刻（:30 など）に関係なく知らせる
assert(calls.filter((x) => x.url.includes("discord")).length === 1, "どの時刻の失敗でも Discord に知らせる");
calls = []; await run("0 9 * * *", { ...env, DISCORD_WEBHOOK_URL: undefined }, new Date("2026-11-19T09:00:00Z"));
assert(calls.filter((x) => x.url.includes("discord")).length === 0, "知らせ先が未設定なら何もしない");

// 500 は3回までやり直す
githubStatus = 500; calls = [];
s = await run("0 20 * * *", env, new Date("2026-11-19T20:30:00Z"));
assert(gh().length === 3 && s.failed.length === 1, "500 は3回までやり直す");

for (const path of ["/no/such.xml", "/hp/", "/rss.xml", "/hp/news/juice.xml"]) {
  const r = await watcher.fetch(new Request("https://hello-rss.example" + path));
  assert(r.status === 302 && r.headers.get("location") === "https://hello-rss.example/", `無いパス ${path} はトップへ302`);
}

// ---- 選択式フィード（/custom.xml）。ASSETS は、rss.py が作った data/ を読む偽物
import { readFileSync, existsSync } from "node:fs";
const dataDir = process.env.PUBLIC_DIR;
if (dataDir) {
  const cenv = { ASSETS: { fetch: async (req) => {
    const p = dataDir + new URL(req.url).pathname;
    return existsSync(p) ? new Response(readFileSync(p)) : new Response("no", { status: 404 });
  } } };
  const get = async (qs) => watcher.fetch(new Request("https://hello-rss.example/custom.xml" + qs), cenv);
  const items = (xml) => (xml.match(/<item>/g) || []).length;

  let r = await get("?site=hp&kind=news");
  let x = await r.text();
  assert(r.status === 200 && r.headers.get("content-type").startsWith("application/rss+xml") && items(x) === 50, "条件なし: 最新50件のRSS");
  r = await get("?site=hp&kind=news&who=angerme");
  x = await r.text();
  assert(items(x) > 0 && x.includes("<title>ハロプロ お知らせ ― アンジュルム</title>"), "who=angerme: アンジュルムの記事だけ・題名に選んだ名前");
  const ids = [...x.matchAll(/<guid[^>]*>([^<]*)</g)].map((m) => m[1]);
  const all = JSON.parse(readFileSync(dataDir + "/data/hp-news.json")).items;
  assert(ids.every((g) => all.find((i) => i.guid === g).who.includes("angerme")), "返った記事は全部、アンジュルムの記事");
  r = await get("?site=mline&kind=news&who=inabamanaka&cat=ticket,goods");
  x = await r.text();
  assert(r.status === 200 && x.includes("M-line club FC ニュース ― 稲場愛香（チケット・グッズ）".replace("稲場愛香", (JSON.parse(readFileSync(dataDir + "/data/options.json")).sources.find((q) => q.site === "mline").who.find((w) => w.key === "inabamanaka") || {}).label)), "M-line（個人）× カテゴリ");
  const up = JSON.parse(readFileSync(dataDir + "/data/options.json")).sources.find((q) => q.site === "upfront" && q.kind === "news").who[0];
  r = await get("?site=upfront&kind=news&who=" + up.key);
  assert(r.status === 200 && (await r.text()).includes(up.label), `アップフロントの人（${up.label}）`);
  r = await get("?site=upfront&kind=schedule");
  assert(r.status === 200, "アップフロントのスケジュール");
  r = await get("?site=mline&kind=news&who=angerme");
  assert(r.status === 400, "M-line に、ハロプロのグループは選べない（サイトをまたがない）");
  // ---- ハロショ: 公式のWordPressのRSSを取りに行って、タイトルだけにして返す（公式サイトへの本物のアクセス）
  const sample = '<rss><channel><item><title><![CDATA[A &amp; B『題』]]></title><link>https://x/1</link><guid isPermaLink="false">https://x/?p=1</guid><pubDate>Thu, 19 Nov 2026 09:30:37 +0000</pubDate><category><![CDATA[グッズ情報]]></category><category>Juice=Juice</category><description>d</description><content:encoded><![CDATA[<img src="a.jpg">長い本文]]></content:encoded></item></channel></rss>';
  const pi = parseWpFeed(sample);
  assert(pi.length === 1 && pi[0].title === "A & B『題』" && pi[0].date === "2026-11-19T09:30:37.000Z" && pi[0].categories.join() === "グッズ情報,Juice=Juice" && pi[0].html === "", "公式のRSSの読み取り（CDATA・実体参照・カテゴリ・本文は捨てる）");
  r = await get("?site=helloshop&kind=news");
  x = await r.text();
  assert(r.status === 200 && items(x) >= 20 && !x.includes("<description>" + "&lt;img") && !/<item>[\s\S]*?<description>/.test(x), "ハロショ: 30件ほど・タイトルだけ（本文の description が無い）");
  r = await get("?site=helloshop&kind=news&who=beyooooonds");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("<category>BEYOOOOONDS</category>")), "ハロショ: タグで絞る（全部 BEYOOOOONDS の記事）");
  r = await get("?site=helloshop&kind=news&who=angerme&cat=goods");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("<category>グッズ情報</category>") && b.includes("<category>アンジュルム</category>")), "ハロショ: グループ × カテゴリ（両方に合う記事だけ）");
  assert(x.includes("<title>ハロショ 新着情報（タイトルだけ） ― アンジュルム（グッズ情報）</title>"), "ハロショ: 題名に選んだ条件");
  r = await get("?site=helloshop&kind=news&q=" + encodeURIComponent("譜久村"));
  x = await r.text();
  assert(r.status === 200 && items(x) > 0 && x.includes("「譜久村」"), "ハロショ: キーワード（公式の検索）");
  // 古い記事は出さない（「譜久村」は、2021年からの記事が30件ほどヒットするが、最近半年のものだけになる）
  r = await get("?site=helloshop&kind=news&q=" + encodeURIComponent("譜久村"));
  x = await r.text();
  const old = [...x.matchAll(/<pubDate>([^<]*)</g)].map((m) => new Date(m[1]).getTime()).filter((t) => t < Date.now() - 181 * 86400000);
  assert(r.status === 200 && old.length === 0 && x.includes("最近180日"), "ハロショ: 最近180日より古い記事は出さない");
  r = await get("?site=helloshop&kind=news&who=nobody");
  assert(r.status === 400, "ハロショ: 知らないタグは400");
  r = await get("?site=helloshop&kind=news&cat=nothing");
  assert(r.status === 400, "ハロショ: 知らないカテゴリは400");
  r = await get("?site=helloshop&kind=news&q=" + "あ".repeat(41));
  assert(r.status === 400, "ハロショ: 長すぎるキーワードは400");
  r = await get("?site=hp&kind=news&q=abc");
  assert(r.status === 400, "キーワードを使えないサイトでは、キーワードは400");
  r = await get("?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT");
  x = await r.text();
  assert(x.includes("アンジュルム・Juice=Juice（イベント）") && [...x.matchAll(/<guid[^>]*>([^<]*)</g)].every((m) => { const i = all.find((j) => j.guid === m[1]); return i.cats.includes("EVENT") && (i.who.includes("angerme") || i.who.includes("juicejuice")); }), "複数のグループ × カテゴリ: どれか＋両方");
  r = await get("?site=hp&kind=schedule&cat=RADIO");
  assert(r.status === 200, "スケジュールのカテゴリ");
  r = await get("?site=jproom&kind=news&who=mizuki_fukumura");
  assert(r.status === 200 && (await r.text()).includes("譜久村聖"), "JPROOM（個人）");
  r = await get("?site=fc&kind=news&cat=goods");
  assert(r.status === 200, "ファンクラブのニュース");

  r = await get("?site=hp");
  assert(r.status === 400, "kind が無いと400");
  r = await get("?site=hp&kind=music");
  assert(r.status === 400, "知らない kind は400");
  r = await get("?site=hp&kind=news&who=nobody");
  assert(r.status === 400 && (await r.text()).includes("who=nobody"), "選べない人・グループは400（誤ったURLを空のフィードにしない）");
  r = await get("?site=hp&kind=news&cat=TV");
  assert(r.status === 400, "お知らせに無いカテゴリは400");
  r = await get("?site=hp&kind=news&who=" + Array.from({ length: 50 }, (_, i) => "a" + i).join(","));
  assert(r.status === 400, "選びすぎは400");
  r = await watcher.fetch(new Request("https://hello-rss.example/custom.xml?site=hp&kind=news"), { ASSETS: { fetch: async () => new Response("x", { status: 500 }) } });
  assert(r.status === 503, "データが読めないときは503");
  // サイトをまたがない: hp のカテゴリ・人を jproom では選べない
  r = await get("?site=jproom&kind=news&who=angerme");
  assert(r.status === 400, "サイトをまたいだ指定（jproom に hp のグループ）は400");
}
