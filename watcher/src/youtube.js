// YouTube（ハロプロ公式のチャンネル）: 保存せず、リーダーが取りに来たときに、選んだチャンネルのRSSを取りに行って、題名だけにして返す。
//   RSS: https://www.youtube.com/feeds/videos.xml?channel_id=UC…（最新15件・公式。Cache-Control は15分）
//   チャンネルのIDは、公式の一覧のチャンネルのページから rss.py が引いて options.json に入れる（src.who[].channels）
//   動画とShortsは、リンクの形（/shorts/）で見分ける（cats: "video" / "shorts"）
//   embed=1 のときだけ、公式の埋め込みプレーヤー（youtube-nocookie.com/embed/{動画のID}）を本文に入れる。説明文やサムネイルは持たない
//   同じRSSは、Cloudflare のキャッシュで15分使い回す

const UA = "hello-rss (youtube title-only feed)";
const CACHE_SEC = 900;

const decodeXml = (t) => t
  .replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, "$1")
  .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");

export function parseAtom(xml) {
  const items = [];
  for (const chunk of xml.split("<entry>").slice(1)) {
    const b = chunk.split("</entry>")[0];
    const one = (tag) => { const m = b.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`)); return m ? decodeXml(m[1]).trim() : ""; };
    const link = (b.match(/<link rel="alternate" href="([^"]+)"/) || [])[1];
    const id = one("yt:videoId");
    const date = new Date(one("published"));
    const title = one("title");
    if (!title || !link || !id || Number.isNaN(date.getTime())) continue;
    items.push({ id, title, link: decodeXml(link), date: date.toISOString(), short: link.includes("/shorts/") });  // 説明（media:description）は読まない
  }
  return items;
}

async function fetchChannel(id) {
  const r = await fetch(`https://www.youtube.com/feeds/videos.xml?channel_id=${encodeURIComponent(id)}`, {
    headers: { "User-Agent": UA }, cf: { cacheTtl: CACHE_SEC, cacheEverything: true },
  });
  if (!r.ok) throw new Error(`${id}: ${r.status}`);
  return parseAtom(await r.text());
}

const embedHtml = (id) => `<iframe width="560" height="315" src="https://www.youtube-nocookie.com/embed/${id}" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>`;

// src.who: [{key, label, channels: [チャンネルのID]}]。選んだもの（who）のチャンネルだけ取りに行く。何も選ばなければ全部
export async function liveItems(src, who, { embed = false } = {}) {
  const selected = who.length ? src.who.filter((w) => who.includes(w.key)) : src.who;
  const ids = [...new Set(selected.flatMap((w) => w.channels || []))];
  const results = await Promise.allSettled(ids.map(fetchChannel));
  if (ids.length && !results.some((r) => r.status === "fulfilled")) throw new Error("チャンネルのRSSを1つも読み込めませんでした");
  const out = [];
  results.forEach((r, n) => {
    if (r.status !== "fulfilled") return;  // 読めなかったチャンネルは飛ばす（ほかは出す）
    const w = selected.find((x) => (x.channels || []).includes(ids[n]));
    for (const it of r.value) {
      out.push({
        guid: `yt:${it.id}`, title: `[${w.label}] ${it.title}`, name: it.title, link: it.link, date: it.date,
        html: embed ? embedHtml(it.id) : "", categories: [w.label, it.short ? "Shorts" : "動画"],
        cats: [it.short ? "shorts" : "video"], who: [w.key],
      });
    }
  });
  const seen = new Set();
  return out.filter((i) => !seen.has(i.guid) && seen.add(i.guid)).sort((a, b) => b.date.localeCompare(a.date));
}
