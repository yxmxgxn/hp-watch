// 手元の確認: NODE_USE_ENV_PROXY=1 node watcher/test.mjs
// 公式サイトへの HEAD は本物、GitHub と Discord への呼び出しは偽物
import watcher, { run, builtRecently } from "./src/index.js";
import { parseRss } from "./src/ameblo.js";
import { parseAtom } from "./src/youtube.js";
import { compact, norm as normJa } from "./src/text.js";

const realFetch = globalThis.fetch;
let calls = [];
let githubStatus = 204;
let failAmeba = false;
let failYoutube = false;
globalThis.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (failAmeba && u.startsWith("https://rssblog.ameba.jp/")) return new Response("x", { status: 500 });
  if (failYoutube && u.startsWith("https://www.youtube.com/feeds/")) return new Response("x", { status: 500 });
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
  const hpNews = data("helloproject-news");
  assert(items(x) > 0 && x.includes("<title>ハロプロ お知らせ ― アンジュルム</title>") && guids(x).every((g) => hpNews.find((i) => i.guid === g).who.includes("angerme")), "who=angerme: アンジュルムの記事だけ・題名に選んだ名前");
  r = await get("?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT");
  x = await r.text();
  assert(x.includes("アンジュルム・Juice=Juice（イベント）") && guids(x).every((g) => { const i = hpNews.find((j) => j.guid === g); return i.cats.includes("EVENT") && (i.who.includes("angerme") || i.who.includes("juicejuice")); }), "複数のグループ × カテゴリ: どれか＋両方");
  r = await get("?site=jproom&kind=news&who=mizuki_fukumura");
  assert(r.status === 200 && (await r.text()).includes("譜久村聖"), "JPROOM（個人）");
  const ml = options.find((q) => q.site === "upfc-m-line").who[0];
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
  assert(guids(await r.text()).every((g) => norm(data("helloproject-schedule").find((i) => i.guid === g).name).includes("radio")), "スケジュールも、題名だけを探す（[RADIO] の印は探さない）");
  r = await get("?site=hp&kind=news&q=a+b+c+d+e+f");
  assert(r.status === 400, "言葉が多すぎる（6個）は400");
  r = await get("?site=hp&kind=news&x=" + "あ".repeat(61));
  assert(r.status === 400, "言葉が長すぎる（61文字）は400");
  assert(await titleOf("?site=hp&kind=news&who=angerme&cat=EVENT&q=" + encodeURIComponent("ツアー") + "&x=" + encodeURIComponent("抽選")) === "ハロプロ お知らせ ― アンジュルム（イベント） 「ツアー」 除く「抽選」", "題名: 全部指定");

  // --- C: ハロショ（公式のRSSから溜めた記事を、タイトルだけで返す）
  r = await get("?site=helloshop&kind=news");
  x = await r.text();
  assert(r.status === 200 && items(x) >= 30 && !/<item>[\s\S]*?<description>/.test(x), "ハロショ: 溜めた30件以上（新着で増える）・タイトルだけ（記事に本文 description が無い）");
  r = await get("?site=helloshop&kind=news&who=beyooooonds");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("<category>BEYOOOOONDS</category>")), "ハロショ: タグで絞る");
  r = await get("?site=helloshop&kind=news&who=angerme&cat=goods");
  x = await r.text();
  assert(x.split("<item>").slice(1).every((b) => b.includes("<category>グッズ情報</category>") && b.includes("<category>アンジュルム</category>")) && x.includes("アンジュルム（グッズ情報）"), "ハロショ: グループ × カテゴリ");
  r = await get("?site=helloshop&kind=news&x=" + encodeURIComponent("生写真"));
  assert(guids(await r.text()).every((g) => !data("helloshop-news").find((i) => i.guid === g).name.includes("生写真")), "ハロショ: 含まない言葉（マイナス検索）");

  // --- site= の名前は、公式のURLの名前。昔の名前も、今の名前として受け付ける（登録済みのURLが切れない）
  for (const [oldName, newName] of Object.entries({ hp: "helloproject", jproom: "jp-r", upfront: "up-front-create", mline: "upfc-m-line", fc: "upfc-helloproject" })) {
    const ra = await get(`?site=${oldName}&kind=news`), rb = await get(`?site=${newName}&kind=news`);
    const ga = guids(await ra.text()), gb = guids(await rb.text());
    assert(ra.status === 200 && rb.status === 200 && ga.length > 0 && ga.join() === gb.join(), `site=${oldName}（昔の名前）は site=${newName}（公式のURLの名前）と同じ`);
  }
  assert(options.map((q) => q.site).every((n) => !["hp", "jproom", "upfront", "fc", "mline"].includes(n)), "選択肢の site は、公式のURLの名前だけ（昔の名前は出さない）");
  r = await get("?site=hp");
  assert(r.status === 400 && (await r.text()).includes("site=helloproject&kind=news"), "エラーの案内も、今の名前");

  // --- アップフロント: 人の選択肢は、ハロプロ関係の名簿の人だけ。名簿に無い人の記事も、全部・言葉の検索には出る
  const up = options.find((q) => q.site === "up-front-create" && q.kind === "schedule");
  const upLabels = up.who.map((w) => w.label);
  assert(upLabels.includes("鈴木愛理") && upLabels.includes("高橋愛") && upLabels.includes("藤本美貴") && !upLabels.includes("堀内孝雄") && !upLabels.includes("松原健之") && !upLabels.includes("ブラザーズ5"), "アップフロント: 人の選択肢は、ハロプロ関係の人だけ（歌手・グループなどは出ない）");
  const upn = options.find((q) => q.site === "up-front-create" && q.kind === "news");
  assert(!upn.who.some((w) => ["堀内孝雄", "松原健之", "ブラザーズ5"].includes(w.label)) && upn.who.length > 5, "アップフロントのお知らせも、人の選択肢は名簿の人だけ");
  r = await get("?site=upfront&kind=news&q=" + encodeURIComponent("松原健之"));
  assert(r.status === 200 && items(await r.text()) > 0, "名簿に無い人の記事も、言葉の検索には出る");

  // --- アメブロ（ハロプロのブログ。保存せず、取りに来たときに公式のRSSを取りに行く。本物のアクセス）
  const am = options.find((q) => q.site === "ameblo");
  assert(am && am.live && am.general === false && am.cat.length === 0 && am.who.filter((w) => w.type === "group").length >= 7 && am.who.filter((w) => w.type === "person").length >= 50 && am.who.every((w) => w.blogs.length > 0), "アメブロ: 選択肢（グループ・メンバーに、ブログのIDが付く）");
  const sampleRss = '<rss><channel><item><title><![CDATA[シチュー　川名凜 &amp; 友達]]></title><description>長い本文</description><link>https://ameblo.jp/x/entry-1.html</link><pubDate>Thu, 19 Nov 2026 13:22:00 +0000</pubDate></item><item><title>題なし</title><link>https://ameblo.jp/x/entry-2.html</link><pubDate>不正</pubDate></item></channel></rss>';
  const pr = parseRss(sampleRss);
  assert(pr.length === 1 && pr[0].title === "シチュー　川名凜 & 友達" && pr[0].date === "2026-11-19T13:22:00.000Z" && !("description" in pr[0]), "アメブロのRSSの読み取り（CDATA・実体参照。日付が不正な記事は捨てる。本文は読まない）");
  assert(compact("川名 凜") === compact("川名凜") && compact("ＯＣＨＡ　ＮＯＲＭＡ") === "ochanorma", "名前の照合: 空白・全角半角・大文字小文字をそろえる");
  assert(compact("山\uFA11愛生") === compact("山崎愛生") && compact("西\uFA11美空") === compact("西崎美空") && compact("櫻井梨央") === compact("桜井梨央") && compact("有澤一華") === compact("有沢一華") && compact("川嶋美楓") === compact("川島美楓"), "名前の照合: 異体字（﨑／崎、櫻／桜、澤／沢、嶋／島）をそろえる");
  r = await get("?site=hp&kind=news&q=" + encodeURIComponent("櫻井"));
  assert(r.status === 200, "含む言葉も、異体字をそろえる（櫻井／桜井）");
  r = await get("?site=ameblo&kind=blog");
  x = await r.text();
  assert(r.status === 200 && items(x) === 50 && x.split("<item>").slice(1).every((b) => /<title>\[[^\]]+\] /.test(b) && !b.includes("<description>")), "アメブロ: 全部 → 最新50件・題名の先頭にグループ名・本文なし");
  r = await get("?site=ameblo&kind=blog&who=angerme");
  x = await r.text();
  assert(items(x) > 0 && [...x.matchAll(/<link>([^<]*)</g)].slice(1).every((m) => m[1].includes("ameblo.jp/angerme-new")) && x.includes("<title>アメブロ（ハロプロ） ブログ（題名だけ） ― アンジュルム</title>"), "アメブロ: グループ → そのグループのブログの記事だけ");
  // メンバー: 題名にその名前がある記事だけ（記事の多いグループのメンバーを順に試して、1人でも見つかればよい）
  let found = null;
  for (const w of am.who.filter((q) => q.type === "person" && q.blogs.includes("angerme-new"))) {
    const rr = await get(`?site=ameblo&kind=blog&who=${w.key}`);
    const xx = await rr.text();
    if (items(xx) > 0) { found = { w, xx }; break; }
  }
  assert(found && found.xx.split("<item>").slice(1).every((b) => compact(b.match(/<title>([^<]*)</)[1]).includes(compact(found.w.label))), `アメブロ: メンバー（${found && found.w.label}）→ 題名にその名前がある記事だけ`);
  r = await get("?site=ameblo&kind=blog&who=juicejuice&x=" + encodeURIComponent("ライブ"));
  assert(r.status === 200 && !(await r.text()).includes("ライブ</title>".slice(0, 0) + "[Juice=Juice] ライブ"), "アメブロ: 含まない言葉");
  r = await get("?site=ameblo&kind=blog&who=ochanorma&q=" + encodeURIComponent("zzzzzz存在しない言葉"));
  assert(r.status === 200 && items(await r.text()) === 0, "アメブロ: 含む言葉に合う記事が無ければ0件（エラーにしない）");
  r = await get("?site=ameblo&kind=blog&general=1");
  assert(r.status === 400, "アメブロ: 全体のもの（general）は指定できない（400）");
  r = await get("?site=ameblo&kind=blog&cat=news");
  assert(r.status === 400, "アメブロ: カテゴリは無い（400）");
  r = await get("?site=ameblo&kind=blog&who=nobody");
  assert(r.status === 400, "アメブロ: 知らない人は400");
  failAmeba = true;
  r = await get("?site=ameblo&kind=blog&who=angerme");
  assert(r.status === 502, "アメブロ: ブログのRSSが1つも読めなければ502");
  failAmeba = false;

  // --- YouTube（ハロプロ公式のチャンネル。保存せず、取りに来たときに公式のRSSを取りに行く。本物のアクセス）
  const yt = options.find((q) => q.site === "youtube");
  assert(yt && yt.live && yt.embed && yt.general === false && yt.who.length >= 15 && yt.who.every((w) => /^UC[\w-]{22}$/.test(w.channels[0])) && yt.cat.map((c) => c.key).join() === "video,shorts", "YouTube: 選択肢（チャンネルのID付き・動画/Shorts）");
  const sampleAtom = '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>AAAAAAAAAAA</yt:videoId><title>歌って &amp; 踊る</title><link rel="alternate" href="https://www.youtube.com/shorts/AAAAAAAAAAA"/><published>2026-11-19T10:00:00+00:00</published><media:group><media:description>長い説明</media:description></media:group></entry><entry><yt:videoId>BBBBBBBBBBB</yt:videoId><title>日付なし</title><link rel="alternate" href="https://www.youtube.com/watch?v=BBBBBBBBBBB"/><published>不正</published></entry></feed>';
  const pa = parseAtom(sampleAtom);
  assert(pa.length === 1 && pa[0].title === "歌って & 踊る" && pa[0].short === true && pa[0].id === "AAAAAAAAAAA" && !JSON.stringify(pa[0]).includes("説明"), "YouTubeのRSSの読み取り（Shortsはリンクで判定。日付が不正なものは捨てる。説明は読まない）");
  r = await get("?site=youtube&kind=video");
  x = await r.text();
  assert(r.status === 200 && items(x) === 50 && x.split("<item>").slice(1).every((b) => /<title>\[[^\]]+\] /.test(b) && !b.includes("<description>")), "YouTube: 全部 → 最新50件・題名の先頭にチャンネル名・本文なし");
  r = await get("?site=youtube&kind=video&who=angerme");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("<category>アンジュルム</category>")) && x.includes("<title>YouTube（ハロプロ公式） 動画 ― アンジュルム</title>"), "YouTube: チャンネルを選ぶ");
  r = await get("?site=youtube&kind=video&cat=shorts");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => b.includes("/shorts/")), "YouTube: Shorts だけ");
  r = await get("?site=youtube&kind=video&cat=video");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => !b.includes("/shorts/")), "YouTube: 動画だけ（Shortsを除く）");
  r = await get("?site=youtube&kind=video&who=angerme&embed=1");
  x = await r.text();
  assert(items(x) > 0 && x.split("<item>").slice(1).every((b) => /&lt;iframe [^>]*youtube-nocookie\.com\/embed\/[\w-]{11}/.test(b)), "YouTube: embed=1 で、公式の埋め込みプレーヤーを入れる");
  r = await get("?site=hp&kind=news&embed=1");
  assert(r.status === 400, "embed は YouTube だけ（ほかは400）");
  r = await get("?site=youtube&kind=video&embed=2");
  assert(r.status === 400, "embed の値が不正なら400");
  r = await get("?site=youtube&kind=video&general=1");
  assert(r.status === 400, "YouTube: 全体のもの（general）は指定できない（400）");
  failYoutube = true;
  r = await get("?site=youtube&kind=video&who=angerme");
  assert(r.status === 502, "YouTube: チャンネルのRSSが1つも読めなければ502");
  failYoutube = false;

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

// --- 放送の情報を手で入れるページ（鍵つき）。GitHub と Worker の素材（ASSETS）は偽物
{
  const im = await import("./src/import.js");
  assert(im.addDays("2026-11-22", 3) === "2026-11-25" && im.jstDay(new Date("2026-11-22T16:00:00Z")) === "2026-11-23", "日付: 放送日の3日後・JST の今日");
  assert(im.validate({ guid: "x", tver: "" }).error, "項目のIDの形が違えばエラー");
  assert(im.validate({ guid: "helloproject-media-abc", tver: "https://example.com/x" }).error, "Tver 以外のURLはエラー");
  assert(im.validate({ guid: "helloproject-media-abc", tver: "https://tver.jp/episodes/epkbq9top3" }).tver, "Tver のエピソードのURLは通る");
  assert(im.validate({ guid: "helloproject-media-abc", guest: "a\u0007b  " }).guest === "ab", "ゲスト名の制御文字は消す");
  const m = im.merge({ items: [{ guid: "g1", air: "2026-11-01", until: "2026-11-04", tver: "t" }, { guid: "g2", air: "2026-11-20", until: "2026-11-23", tver: "u" }] }, { guid: "g2", air: "2026-11-20", until: "2026-11-23", tver: "v", guest: "" }, "2026-11-23");
  assert(m.items.length === 1 && m.items[0].tver === "v", "期限の切れた分は外し、同じ項目は入れ替える");
  const m2 = im.merge({ items: [{ guid: "g2", air: "2026-11-20", until: "2026-11-23", tver: "u" }] }, { guid: "g2", air: "2026-11-20", until: "2026-11-23", tver: "", guest: "" }, "2026-11-21");
  assert(m2.items.length === 0, "両方空なら、その項目は外す");

  const prev = globalThis.fetch;
  const log = [];
  let putBody = null;
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    if (u.startsWith("https://api.github.com/")) {
      log.push({ url: u, opts });
      if (u.includes("/contents/manual/import.json")) {
        if (opts.method === "PUT") { putBody = JSON.parse(opts.body); return new Response("{}", { status: 201 }); }
        return new Response("Not Found", { status: 404 });
      }
      if (u.includes("/dispatches")) return new Response(null, { status: 204 });
    }
    return realFetch(url, opts);
  };
  try {
    const assets = { fetch: async (req) => {
      const p = new URL(req.url).pathname;
      if (p === "/data/options.json") return Response.json({ sources: [{ site: "helloproject", siteLabel: "ハロプロ", kind: "schedule", file: "/data/helloproject-schedule.json" }] });
      if (p === "/data/helloproject-schedule.json") return Response.json({ items: [{ guid: "helloproject-media-abc123", title: "番組", name: "番組名", air: "2026-11-16", link: "x", date: "2026-11-17T00:00:00Z" }] });
      return new Response("no", { status: 404 });
    } };
    const kenv = { GH_REPO: "o/r", GH_TOKEN: "tok", GH_DATA_TOKEN: "dtok", DASHBOARD_KEY: "secret-key-1", ASSETS: assets };
    const post = (b) => watcher.fetch(new Request("https://hello-rss.example/import/save", { method: "POST", body: JSON.stringify(b) }), kenv);

    let r = await watcher.fetch(new Request("https://hello-rss.example/import"), kenv);
    const html = await r.text();
    assert(r.status === 200 && html.includes("放送の情報を入れる") && !html.includes("secret-key-1"), "入力ページ: 開ける・鍵の値は出ない");

    r = await post({ key: "wrong", guid: "helloproject-media-abc123", tver: "", guest: "" });
    assert(r.status === 403 && putBody === null, "鍵が違えば403。書き込みもしない");

    r = await post({ key: "secret-key-1", guid: "helloproject-media-abc123", tver: "https://tver.jp/episodes/epkbq9top3", guest: "ゲスト" });
    const j = await r.json();
    assert(r.status === 200 && j.until === "2026-11-19", "保存: 放送日（11/16）の3日後（11/19）まで");
    const saved = JSON.parse(Buffer.from(putBody.content, "base64").toString("utf8"));
    assert(saved.items.length === 1 && saved.items[0].guid === "helloproject-media-abc123" && saved.items[0].guest === "ゲスト", "書き込んだファイルの中身（base64 を戻すと一致）");
    assert(log.some((c) => c.url.endsWith("/actions/workflows/rss.yml/dispatches")), "保存のあと rss.yml を呼ぶ");

    r = await post({ key: "secret-key-1", guid: "helloproject-media-zzz", tver: "", guest: "" });
    assert(r.status === 400, "フィードに無い項目は400");
  } finally {
    globalThis.fetch = prev;
  }
}
