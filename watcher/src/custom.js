// 選択式フィード: /custom.xml?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT
//   site / kind … 必須。1つのサイトの、お知らせ（news）かスケジュール（schedule）のどちらか（またがない）
//   who / cat   … 任意。カンマ区切りで複数。who は人・グループ、cat はカテゴリ。同じ項目の中は「どれか」、who と cat は「両方」。
//                 空なら全部
// 元データは公開のたびに rss.py が置く data/{site}-{kind}.json（人・グループ・カテゴリ付きの記事）と data/options.json
// 選べる値は options.json にあるものだけ。それ以外は400を返す（誤った条件のURLが、空のフィードとして登録されるのを防ぐ）

const LIMIT = 50;
const MAX_VALUES = 40;

const text = (status, body) => new Response(body + "\n", { status, headers: { "content-type": "text/plain; charset=utf-8" } });
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

async function readJson(env, path) {
  const r = await env.ASSETS.fetch(new Request(new URL(path, "https://assets.invalid")));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

const list = (v) => [...new Set((v || "").split(",").map((x) => x.trim()).filter(Boolean))];

export function renderRss({ title, description, link, self, items }) {
  const out = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    "<channel>",
    `<title>${esc(title)}</title>`,
    `<link>${esc(link)}</link>`,
    `<description>${esc(description)}</description>`,
    "<language>ja</language>",
    `<atom:link href="${esc(self)}" rel="self" type="application/rss+xml"/>`,
  ];
  if (items.length) out.push(`<lastBuildDate>${new Date(items[0].date).toUTCString()}</lastBuildDate>`);
  for (const i of items) {
    out.push(
      "<item>",
      `<title>${esc(i.title)}</title>`,
      `<link>${esc(i.link)}</link>`,
      `<guid isPermaLink="false">${esc(i.guid)}</guid>`,
      `<pubDate>${new Date(i.date).toUTCString()}</pubDate>`,
      ...[...new Set(i.categories || [])].map((c) => `<category>${esc(c)}</category>`),
      `<description>${esc(i.html)}</description>`,
      "</item>",
    );
  }
  out.push("</channel>", "</rss>", "");
  return out.join("\n");
}

export async function customFeed(req, env) {
  const u = new URL(req.url);
  let options;
  try {
    options = await readJson(env, "/data/options.json");
  } catch (e) {
    return text(503, "選択肢のデータを読み込めませんでした。しばらくしてからもう一度お試しください");
  }
  const site = u.searchParams.get("site"), kind = u.searchParams.get("kind");
  const src = options.sources.find((s) => s.site === site && s.kind === kind);
  if (!src) {
    const ok = options.sources.map((s) => `site=${s.site}&kind=${s.kind}`).join(" / ");
    return text(400, `site と kind を指定してください（${ok}）`);
  }
  const who = list(u.searchParams.get("who")), cat = list(u.searchParams.get("cat"));
  if (who.length > MAX_VALUES || cat.length > MAX_VALUES) return text(400, "選びすぎです");
  const badWho = who.filter((k) => !src.who.some((o) => o.key === k));
  const badCat = cat.filter((k) => !src.cat.some((o) => o.key === k));
  if (badWho.length || badCat.length) {
    return text(400, `選べない値があります: ${[...badWho.map((k) => `who=${k}`), ...badCat.map((k) => `cat=${k}`)].join(", ")}\n選べる値は /custom/ の画面で確かめてください`);
  }

  let data;
  try {
    data = await readJson(env, src.file);
  } catch (e) {
    return text(503, "記事のデータを読み込めませんでした。しばらくしてからもう一度お試しください");
  }
  const items = data.items
    .filter((i) => (!who.length || (i.who || []).some((k) => who.includes(k))) && (!cat.length || (i.cats || []).some((c) => cat.includes(c))))
    .slice(0, LIMIT);

  const label = (arr, sel) => src[arr].filter((o) => sel.includes(o.key)).map((o) => o.label);
  const names = who.length ? label("who", who).join("・") : "全員";
  const cats = cat.length ? `（${label("cat", cat).join("・")}）` : "";
  const origin = u.origin;
  return new Response(renderRss({
    title: `${src.siteLabel} ${src.kindLabel} ― ${names}${cats}`,
    description: `${src.siteLabel}の${src.kindLabel}から、選んだ条件の記事だけ・非公式`,
    link: `${origin}/custom/`,
    self: u.href,
    items,
  }), {
    headers: { "content-type": "application/rss+xml; charset=utf-8", "cache-control": "public, max-age=600" },
  });
}
