// 選択式フィード: /custom.xml?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT&q=ツアー&x=抽選
//   site / kind … 必須。1つのサイトの、お知らせ（news）かスケジュール（schedule）のどちらか（またがない）
//   who / cat   … 任意。カンマ区切りで複数。who は人・グループ、cat はカテゴリ。同じ項目の中は「どれか」
//   general     … 特定の人・グループに紐づかない「全体のもの」の扱い。1＝全体のものだけ（who とは一緒に使えない）／0＝全体のものを除く
//                 （例: who=angerme&general=0 で、アンジュルムだけのタグの記事）。無ければ、含める
//   q / x       … 題名（name）に含まれる言葉。q は「含む」（スペース区切りは全部を含む）、x は「含まない」（どれかを含むものを除く）
//   上の条件は、全部を満たす記事だけ。空なら全部
// 元データは公開のたびに rss.py が置く data/{site}-{kind}.json（人・グループ・カテゴリ・題名付きの記事）と data/options.json
// 選べる値は options.json にあるものだけ。それ以外は400を返す（誤った条件のURLが、空のフィードとして登録されるのを防ぐ）
// 言葉の判定は、全角・半角と大文字・小文字・異体字（﨑／崎など。text.js）をそろえて、題名だけを見る。本文は持たない・公開しない

import { liveItems } from "./ameblo.js";
import { norm } from "./text.js";

const LIMIT = 50;
const MAX_VALUES = 40;
const MAX_WORDS = 5;    // q・x それぞれの言葉の数
const MAX_QUERY = 60;   // q・x それぞれの長さ

const text = (status, body) => new Response(body + "\n", { status, headers: { "content-type": "text/plain; charset=utf-8" } });
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

async function readJson(env, path) {
  const r = await env.ASSETS.fetch(new Request(new URL(path, "https://assets.invalid")));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

const list = (v) => [...new Set((v || "").split(",").map((x) => x.trim()).filter(Boolean))];
const words = (v) => norm(v).split(/\s+/).filter(Boolean);

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
  const gRaw = u.searchParams.get("general");
  if (gRaw !== null && !["1", "true", "0", "false"].includes(gRaw)) return text(400, "general は 1（全体のものだけ）か 0（全体のものを除く）です");
  const general = ["1", "true"].includes(gRaw || "");        // 全体のものだけ
  const noGeneral = ["0", "false"].includes(gRaw || "");     // 全体のものを除く
  const qRaw = (u.searchParams.get("q") || "").trim(), xRaw = (u.searchParams.get("x") || "").trim();
  if (who.length > MAX_VALUES || cat.length > MAX_VALUES) return text(400, "選びすぎです");
  if (qRaw.length > MAX_QUERY || xRaw.length > MAX_QUERY || words(qRaw).length > MAX_WORDS || words(xRaw).length > MAX_WORDS) {
    return text(400, `言葉は、${MAX_WORDS}個・${MAX_QUERY}文字までです`);
  }
  if (general && who.length) return text(400, "general（全体のものだけ）と who（人・グループ）は、一緒に使えません");
  if ((general || noGeneral) && src.general === false) return text(400, "この組み合わせでは、general（全体のもの）は指定できません");
  const badWho = who.filter((k) => !src.who.some((o) => o.key === k));
  const badCat = cat.filter((k) => !src.cat.some((o) => o.key === k));
  if (badWho.length || badCat.length) {
    return text(400, `選べない値があります: ${[...badWho.map((k) => `who=${k}`), ...badCat.map((k) => `cat=${k}`)].join(", ")}\n選べる値は /custom/ の画面で確かめてください`);
  }

  let data;
  if (src.live) {
    // 保存しない（アメブロ）: 取りに来たときに、選んだブログのRSSを取りに行く（ameblo.js）
    try {
      data = { items: await liveItems(src, who) };
    } catch (e) {
      return text(502, `${src.siteLabel}のRSSを読み込めませんでした（${e.message}）。しばらくしてからもう一度お試しください`);
    }
  } else {
    try {
      data = await readJson(env, src.file);
    } catch (e) {
      return text(503, "記事のデータを読み込めませんでした。しばらくしてからもう一度お試しください");
    }
  }
  const inc = words(qRaw), exc = words(xRaw);
  const items = data.items
    .filter((i) => {
      if (general && !i.general) return false;
      if (noGeneral && i.general) return false;
      if (who.length && !(i.who || []).some((k) => who.includes(k))) return false;
      if (cat.length && !(i.cats || []).some((c) => cat.includes(c))) return false;
      if (!inc.length && !exc.length) return true;
      const name = norm(i.name || i.title);
      return inc.every((w) => name.includes(w)) && !exc.some((w) => name.includes(w));
    })
    .slice(0, LIMIT);

  // 題名の条件: 全体のもの／グループ（カテゴリ）／「含む言葉」／除く「含まない言葉」。どれも選んでいなければ「全部」
  const label = (arr, sel) => src[arr].filter((o) => sel.includes(o.key)).map((o) => o.label);
  const cats = label("cat", cat).join("・");
  const head = general ? "全体のもの" + (cat.length ? `（${cats}）` : "") : who.length ? label("who", who).join("・") + (cat.length ? `（${cats}）` : "") : cats;
  const what = [head, noGeneral ? "全体のものを除く" : "", qRaw ? `「${qRaw}」` : "", xRaw ? `除く「${xRaw}」` : ""].filter(Boolean).join(" ");
  return new Response(renderRss({
    title: `${src.siteLabel} ${src.kindLabel} ― ${what || "全部"}`,
    description: `${src.siteLabel}の${src.kindLabel}から、選んだ条件の記事だけ・非公式`,
    link: `${u.origin}/custom/`,
    self: u.href,
    items,
  }), {
    headers: { "content-type": "application/rss+xml; charset=utf-8", "cache-control": "public, max-age=600" },
  });
}
