// アメブロ（ハロプロのグループ・メンバーのブログ）: 保存せず、リーダーが取りに来たときに、選んだブログのRSSを取りに行って、題名だけにして返す。
//   RSS: https://rssblog.ameba.jp/{ブログのID}/rss20.xml（最新30件。ETag・Last-Modified は無い。カテゴリ（テーマ）も入っていない）
//   ブログのIDと、グループ・メンバーとの対応は、公式のプロフィール（blogSlug）から rss.py が options.json に入れる
//     グループ … そのグループのブログ全部の記事
//     メンバー … そのメンバーのブログのうち、題名にその名前（全角半角・空白・大文字小文字・異体字（﨑／崎など）をそろえて）があるもの
//               （グループのブログは、題名の末尾に書き手の名前を書く書き方が多い。名前を書かない記事は拾えない）
//   同じRSSは、Cloudflare のキャッシュで10分使い回す（何人が使っても、1ブログあたり10分に1回ほど）

import { compact } from "./text.js";

const UA = "hello-rss (ameblo title-only feed)";
const CACHE_SEC = 600;

const decodeXml = (t) => t
  .replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, "$1")
  .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

export function parseRss(xml) {
  const items = [];
  for (const chunk of xml.split("<item>").slice(1)) {
    const b = chunk.split("</item>")[0];
    const one = (tag) => { const m = b.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`)); return m ? decodeXml(m[1]).trim() : ""; };
    const date = new Date(one("pubDate"));
    const title = one("title"), link = one("link");
    if (!title || !link || Number.isNaN(date.getTime())) continue;
    items.push({ title, link, date: date.toISOString() });  // 本文の抜粋（description）は読まない
  }
  return items;
}

export { compact };

async function fetchBlog(id) {
  const r = await fetch(`https://rssblog.ameba.jp/${encodeURIComponent(id)}/rss20.xml`, {
    headers: { "User-Agent": UA }, cf: { cacheTtl: CACHE_SEC, cacheEverything: true },
  });
  if (!r.ok) throw new Error(`${id}: ${r.status}`);
  return parseRss(await r.text());
}

// src.who: [{key, label, type: "group"|"person", blogs: [ブログのID]}]。選んだもの（who）のブログだけ取りに行く。何も選ばなければ全部
export async function liveItems(src, who) {
  const groups = src.who.filter((w) => w.type === "group");
  const persons = src.who.filter((w) => w.type === "person");
  const selected = who.length ? src.who.filter((w) => who.includes(w.key)) : src.who;
  const ids = [...new Set(selected.flatMap((w) => w.blogs || []))];
  const results = await Promise.allSettled(ids.map(fetchBlog));
  const ok = results.filter((r) => r.status === "fulfilled").length;
  if (ids.length && !ok) throw new Error("ブログのRSSを1つも読み込めませんでした");

  const out = [];
  results.forEach((r, n) => {
    if (r.status !== "fulfilled") return;  // 読めなかったブログは飛ばす（ほかは出す）
    const id = ids[n];
    const gs = groups.filter((g) => (g.blogs || []).includes(id));
    const label = gs.length ? gs[0].label : id;
    const people = persons.filter((p) => (p.blogs || []).includes(id)).map((p) => ({ ...p, c: compact(p.label) }));
    for (const it of r.value) {
      const t = compact(it.title);
      const hit = people.filter((p) => p.c.length >= 2 && t.includes(p.c));
      out.push({
        guid: it.link, title: `[${label}] ${it.title}`, name: it.title, link: it.link, date: it.date, html: "",
        categories: [label, ...hit.map((p) => p.label)], cats: [], who: [...gs.map((g) => g.key), ...hit.map((p) => p.key)],
      });
    }
  });
  const seen = new Set();
  return out.filter((i) => !seen.has(i.guid) && seen.add(i.guid)).sort((a, b) => b.date.localeCompare(a.date));
}
