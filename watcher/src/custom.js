// 選択式フィード: /custom.xml?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT
//   site / kind … 必須。1つのサイトの、お知らせ（news）かスケジュール（schedule）のどちらか（またがない）
//   who / cat   … 任意。カンマ区切りで複数。who は人・グループ、cat はカテゴリ。同じ項目の中は「どれか」、who と cat は「両方」。
//                 空なら全部
// 元データは公開のたびに rss.py が置く data/{site}-{kind}.json（人・グループ・カテゴリ付きの記事）と data/options.json
// 選べる値は options.json にあるものだけ。それ以外は400を返す（誤った条件のURLが、空のフィードとして登録されるのを防ぐ）

const LIMIT = 50;
const MAX_VALUES = 40;
const MAX_QUERY = 40;   // キーワード（人名など）の長さ
const LIVE_PAGES = 3;   // 公式のフィードは1ページ10件。3ページ分（30件）まで見る

const text = (status, body) => new Response(body + "\n", { status, headers: { "content-type": "text/plain; charset=utf-8" } });
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

async function readJson(env, path) {
  const r = await env.ASSETS.fetch(new Request(new URL(path, "https://assets.invalid")));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// ---- 公式が WordPress で、RSS（タグ・カテゴリ・検索で絞れる）を出しているサイト。公式のRSSを取りに行って、タイトルだけにして返す
const decodeXml = (t) => t
  .replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, "$1")
  .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

export function parseWpFeed(xml) {
  const items = [];
  for (const chunk of xml.split("<item>").slice(1)) {
    const b = chunk.split("</item>")[0];
    const one = (tag) => { const m = b.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`)); return m ? decodeXml(m[1]).trim() : ""; };
    const date = new Date(one("pubDate"));
    if (!one("title") || Number.isNaN(date.getTime())) continue;
    items.push({
      guid: one("guid") || one("link"), title: one("title"), link: one("link"), date: date.toISOString(), html: "",
      categories: [...b.matchAll(/<category(?:\s[^>]*)?>([\s\S]*?)<\/category>/g)].map((m) => decodeXml(m[1]).trim()).filter(Boolean),
    });
  }
  return items;
}

async function liveFeed(src, u, who, cat, q) {
  const base = src.live.base;
  const url = (page) => {
    const p = new URLSearchParams();
    if (who.length) p.set("tag", who.join(","));
    if (cat.length) p.set("category_name", cat.join(","));
    if (q) p.set("s", q);
    if (page > 1) p.set("paged", String(page));
    const qs = p.toString();
    return `${base}/feed${qs ? "?" + qs : ""}`;
  };
  const get = async (page) => {
    const r = await fetch(url(page), { headers: { "User-Agent": "hello-rss (title-only feed)" }, cf: { cacheTtl: 300, cacheEverything: true } });
    if (!r.ok) throw new Error(`${r.status}`);
    return parseWpFeed(await r.text());
  };
  let pages;
  try {
    pages = await Promise.all(Array.from({ length: LIVE_PAGES }, (_, i) => get(i + 1).catch((e) => (i === 0 ? Promise.reject(e) : []))));
  } catch (e) {
    return text(502, `${src.siteLabel}の公式のRSSを読み込めませんでした（${e.message}）。しばらくしてからもう一度お試しください`);
  }
  const seen = new Set();
  const items = pages.flat().filter((i) => !seen.has(i.guid) && seen.add(i.guid)).slice(0, LIMIT);
  const label = (arr, sel) => src[arr].filter((o) => sel.includes(o.key)).map((o) => o.label);
  const names = who.length ? label("who", who).join("・") : "全部";
  const cats = cat.length ? `（${label("cat", cat).join("・")}）` : "";
  return new Response(renderRss({
    title: `${src.siteLabel} ${src.kindLabel} ― ${names}${cats}${q ? `「${q}」` : ""}`,
    description: `${src.siteLabel}の公式のRSSから、選んだ条件の記事のタイトルだけ・非公式`,
    link: `${u.origin}/custom/`,
    self: u.href,
    items,
  }), { headers: { "content-type": "application/rss+xml; charset=utf-8", "cache-control": "public, max-age=300" } });
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
      ...(i.html ? [`<description>${esc(i.html)}</description>`] : []),  // 本文は、無ければ出さない（タイトルだけのフィード）
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
  const q = (u.searchParams.get("q") || "").trim();
  if (q && !src.keyword) return text(400, "この組み合わせは、キーワード（q）を指定できません");
  if (q.length > MAX_QUERY) return text(400, "キーワードが長すぎます");
  if (who.length > MAX_VALUES || cat.length > MAX_VALUES) return text(400, "選びすぎです");
  const badWho = who.filter((k) => !src.who.some((o) => o.key === k));
  const badCat = cat.filter((k) => !src.cat.some((o) => o.key === k));
  if (badWho.length || badCat.length) {
    return text(400, `選べない値があります: ${[...badWho.map((k) => `who=${k}`), ...badCat.map((k) => `cat=${k}`)].join(", ")}\n選べる値は /custom/ の画面で確かめてください`);
  }

  if (src.live) return liveFeed(src, u, who, cat, q);

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
