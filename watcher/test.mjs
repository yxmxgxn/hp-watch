// 手元の確認: NODE_USE_ENV_PROXY=1 node watcher/test.mjs
// 公式サイトへの HEAD は本物、GitHub と Discord への呼び出しは偽物
import watcher, { run, builtRecently } from "./src/index.js";

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

assert((await builtRecently("https://helloproject.com/schedule/", at(5)))?.getTime() === lm.getTime(), "更新の5分後 → 直近の更新として検知");
assert((await builtRecently("https://helloproject.com/schedule/", at(90))) === null, "更新の90分後 → 検知しない");

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

// ---- ハロショの更新も、時計が見る（公式の /feed の Last-Modified。本物のアクセス）
{
  const hs = await realFetch("https://www.helloshop.info/feed", { method: "HEAD" });
  const hlm = new Date(hs.headers.get("last-modified"));
  assert((await builtRecently("https://www.helloshop.info/feed", new Date(hlm.getTime() + 5 * 60000)))?.getTime() === hlm.getTime(), "ハロショ: 更新の5分後 → 直近の更新として検知");
  assert((await builtRecently("https://www.helloshop.info/feed", new Date(hlm.getTime() + 90 * 60000))) === null, "ハロショ: 更新の90分後 → 検知しない");
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
  const guids = (xml) => [...xml.matchAll(/<guid[^>]*>([^<]*)</g)].map((m) => m[1]);
  const titleOf = async (qs) => ((await (await get(qs)).text()).match(/<title>([^<]*)<\/title>/) || [])[1];
  const data = (name) => JSON.parse(readFileSync(`${dataDir}/data/${name}.json`)).items;
  const options = JSON.parse(readFileSync(dataDir + "/data/options.json")).sources;
  const norm = (t) => t.normalize("NFKC").toLowerCase();

  // --- 基本
  let r = await get("?site=hp&kind=news");
  let x = await r.text();
  assert(r.status === 200 && r.headers.get("content-type").startsWith("application/rss+xml") && items(x) === 50, "条件なし: 最新50件のRSS");
  r = await get("?site=hp&kind=news&who=angerme");
  x = await r.text();
  const hpNews = data("hp-news");
  assert(items(x) > 0 && x.includes("<title>ハロプロ お知らせ ― アンジュルム</title>") && guids(x).every((g) => hpNews.find((i) => i.guid === g).who.includes("angerme")), "who=angerme: アンジュルムの記事だけ・題名に選んだ名前");
  r = await get("?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT");
  x = await r.text();
  assert(x.includes("アンジュルム・Juice=Juice（イベント）") && guids(x).every((g) => { const i = hpNews.find((j) => j.guid === g); return i.cats.includes("EVENT") && (i.who.includes("angerme") || i.who.includes("juicejuice")); }), "複数のグループ × カテゴリ: どれか＋両方");
  r = await get("?site=jproom&kind=news&who=mizuki_fukumura");
  assert(r.status === 200 && (await r.text()).includes("譜久村聖"), "JPROOM（個人）");
  const ml = options.find((q) => q.site === "mline").who[0];
  r = await get(`?site=mline&kind=news&who=${ml.key}&cat=ticket,goods`);
  assert(r.status === 200 && (await r.text()).includes(`M-line club FC ニュース ― ${ml.label}（チケット・グッズ）`), "M-line（個人）× カテゴリ");
  r = await get("?site=fc&kind=news&cat=goods");
  assert(r.status === 200, "ファンクラブのニュース");
  r = await get("?site=hp&kind=schedule&cat=RADIO");
  assert(r.status === 200, "スケジュールのカテゴリ");

  // --- A: 全体のものだけ（特定の人・グループに紐づかない）
  r = await get("?site=hp&kind=news&general=1");
  x = await r.text();
  assert(items(x) > 0 && guids(x).every((g) => hpNews.find((i) => i.guid === g).general) && x.includes("<title>ハロプロ お知らせ ― 全体のもの</title>"), "全体のものだけ（general=1）");
  r = await get("?site=hp&kind=news&general=1&cat=EVENT");
  x = await r.text();
  assert(guids(x).every((g) => { const i = hpNews.find((j) => j.guid === g); return i.general && i.cats.includes("EVENT"); }) && x.includes("全体のもの（イベント）"), "全体のもの × カテゴリ");
  r = await get("?site=hp&kind=news&general=1&who=angerme");
  assert(r.status === 400, "全体のもの と 人・グループは、一緒に使えない（400）");
  // 全体のものを除く（general=0）: タグが付いた記事だけ
  r = await get("?site=hp&kind=news&who=angerme&general=0");
  x = await r.text();
  assert(items(x) > 0 && guids(x).every((g) => { const i = hpNews.find((j) => j.guid === g); return i.who.includes("angerme") && !i.general; }) && x.includes("<title>ハロプロ お知らせ ― アンジュルム 全体のものを除く</title>"), "全体のものを除く（general=0）＋グループ: そのグループだけのタグの記事");
  r = await get("?site=hp&kind=news&who=angerme");
  assert(items(await r.text()) >= items(await (await get("?site=hp&kind=news&who=angerme&general=0")).text()), "除かないときは、全体のものも入る（除いたときより多いか同じ）");
  r = await get("?site=hp&kind=news&general=0");
  assert(guids(await r.text()).every((g) => !hpNews.find((i) => i.guid === g).general), "全体のものを除く（グループを選ばない）");
  r = await get("?site=hp&kind=news&general=x");
  assert(r.status === 400, "general が 1・0 以外は400");
  r = await get("?site=helloshop&kind=news&general=1");
  assert(r.status === 200 && items(await r.text()) > 0, "ハロショの全体のもの（店舗・お知らせなど）");

  // --- B: 題名の言葉（含む・含まない。全角半角・大文字小文字をそろえる）
  const named = hpNews.find((i) => /OCHA NORMA/.test(i.name));
  r = await get("?site=hp&kind=news&q=" + encodeURIComponent("ocha norma"));
  x = await r.text();
  assert(items(x) > 0 && guids(x).every((g) => /ocha/.test(norm(hpNews.find((i) => i.guid === g).name)) && /norma/.test(norm(hpNews.find((i) => i.guid === g).name))), "含む言葉: 大文字小文字をそろえ、スペース区切りは全部を含む");
  assert(x.includes("<title>ハロプロ お知らせ ― 「ocha norma」</title>"), "題名: キーワードだけのときは「全部」を付けない");
  r = await get("?site=hp&kind=news&x=" + encodeURIComponent("OCHA"));
  x = await r.text();
  assert(items(x) > 0 && guids(x).every((g) => !norm(hpNews.find((i) => i.guid === g).name).includes("ocha")) && x.includes("除く「OCHA」"), "含まない言葉: 含むものを除く");
  r = await get("?site=hp&kind=news&q=" + encodeURIComponent("ocha") + "&x=" + encodeURIComponent("norma"));
  assert(guids(await r.text()).every((g) => { const n = norm(hpNews.find((j) => j.guid === g).name); return n.includes("ocha") && !n.includes("norma"); }), "含む と 含まない の組み合わせ（ocha を含み、norma を含まない）");
  r = await get("?site=hp&kind=news&q=" + encodeURIComponent("２０２６"));
  assert(items(await r.text()) === hpNews.filter((i) => norm(i.name).includes("2026")).slice(0, 50).length && hpNews.some((i) => norm(i.name).includes("2026")), "全角の数字（２０２６）も、半角にそろえて探す");
  // 飾り（日付・[NEWS]・[RADIO] など）は、探す対象にしない
  r = await get("?site=hp&kind=news&q=news");
  assert(items(await r.text()) === hpNews.filter((i) => norm(i.name).includes("news")).slice(0, 50).length, "題名の飾り（[NEWS] など）は探さない");
  r = await get("?site=hp&kind=schedule&q=" + encodeURIComponent("RADIO"));
  assert(guids(await r.text()).every((g) => norm(data("hp-schedule").find((i) => i.guid === g).name).includes("radio")), "スケジュールも、題名だけを探す（[RADIO] の印は探さない）");
  r = await get("?site=hp&kind=news&q=a+b+c+d+e+f");
  assert(r.status === 400, "言葉が多すぎる（6個）は400");
  r = await get("?site=hp&kind=news&x=" + "あ".repeat(61));
  assert(r.status === 400, "言葉が長すぎる（61文字）は400");
  assert(await titleOf("?site=hp&kind=news&who=angerme&cat=EVENT&q=" + encodeURIComponent("ツアー") + "&x=" + encodeURIComponent("抽選")) === "ハロプロ お知らせ ― アンジュルム（イベント） 「ツアー」 除く「抽選」", "題名: 全部指定");

  // --- C: ハロショ（公式のRSSから溜めた記事を、タイトルだけで返す）
  r = await get("?site=helloshop&kind=news");
  x = await r.text();
  assert(r.status === 200 && items(x) === 30 && !/<item>[\s\S]*?<description>/.test(x), "ハロショ: 溜めた30件・タイトルだけ（記事に本文 description が無い）");
  r = await get("?site=helloshop&kind=news&who=beyooooonds");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("<category>BEYOOOOONDS</category>")), "ハロショ: タグで絞る");
  r = await get("?site=helloshop&kind=news&who=angerme&cat=goods");
  x = await r.text();
  assert(x.split("<item>").slice(1).every((b) => b.includes("<category>グッズ情報</category>") && b.includes("<category>アンジュルム</category>")) && x.includes("アンジュルム（グッズ情報）"), "ハロショ: グループ × カテゴリ");
  r = await get("?site=helloshop&kind=news&x=" + encodeURIComponent("生写真"));
  assert(guids(await r.text()).every((g) => !data("helloshop-news").find((i) => i.guid === g).name.includes("生写真")), "ハロショ: 含まない言葉（マイナス検索）");

  // --- エラー
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
  r = await get("?site=jproom&kind=news&who=angerme");
  assert(r.status === 400, "サイトをまたいだ指定（jproom に hp のグループ）は400");
  r = await get("?site=mline&kind=news&who=angerme");
  assert(r.status === 400, "M-line に、ハロプロのグループは選べない（サイトをまたがない）");
  r = await watcher.fetch(new Request("https://hello-rss.example/custom.xml?site=hp&kind=news"), { ASSETS: { fetch: async () => new Response("x", { status: 500 }) } });
  assert(r.status === 503, "データが読めないときは503");
}
