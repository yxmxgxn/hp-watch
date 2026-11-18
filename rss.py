"""公式のお知らせJSONから RSS（と一覧ページ・OPML）を作る

  python rss.py --out public [--feed-base https://hello-rss.karin-archive.com]

お知らせは記事ID・公開日・リンクが最初からJSONに入っているので、前回の状態は持たない
（毎回JSONだけから作り直しても、記事ID(guid)が同じなのでリーダー側で重複しない）。

フィード（out 配下）:
  hp/news/{グループslug}.xml, hp/news/all.xml   … ハロー！プロジェクト（グループ別）
  jproom/news/all.xml                           … JPROOM（所属が個人なので全員まとめて1本）
  index.html, all.opml                          … 一覧ページとまとめて登録するファイル
"""
import argparse
import datetime as dt
import json
import os
import sys
from email.utils import format_datetime
from xml.sax.saxutils import escape, quoteattr

import hpwatch

JST = dt.timezone(dt.timedelta(hours=9))
LIMIT = 50  # 1フィードに残す記事数

SITES = {
    "hp": {"label": "ハロプロ", "base": "https://helloproject.com", "by_group": True},
    "jproom": {"label": "JPROOM", "base": "https://www.jp-r.co.jp", "by_group": False},
}


# ---------- 取得 ----------

def fetch_news(base, version, years):
    items = []
    for y in years:
        try:
            items += json.loads(hpwatch.get(f"{base}/json/{version}/{y}_news_list.json"))["items"]
        except Exception as e:  # 年が変わった直後などで前年・今年のどちらかが無いことがある
            print(f"warn: {y}_news_list.json: {e}", file=sys.stderr)
    return items


def recent_years():
    y = dt.datetime.now(JST).year
    return [str(y - 1), str(y)]


# ---------- 記事 ----------

def item_link(base, x):
    """リンク欄があればそれ。無ければ /news/{id}/（NEWS の記事にだけ存在する）"""
    link = (x.get("link") or {}).get("link")
    if link:
        return link if link.startswith("http") else base + link
    return f"{base}/news/{x['id']}/"


def to_item(site, base, x):
    title = hpwatch.text_of(x.get("title")).strip()
    tags = [x.get("category") or "NEWS"]
    if x.get("flag") == "UPDATE":
        tags.append("更新")
    date = dt.datetime.fromisoformat(x["date"]).replace(tzinfo=JST)
    return {
        "guid": f"{site}-news-{x['id']}",
        "id": x["id"],
        "title": "".join(f"[{t}]" for t in tags) + " " + title,
        "link": item_link(base, x),
        "date": date,
        "categories": [x.get("category") or "NEWS"] + list(x.get("tags") or []),
        "groups": [str(i) for i in x.get("artistsSearch") or []],
    }


def newest(items):
    # 同じ日の中は ID の大きい（新しい）順
    return sorted(items, key=lambda i: (i["date"], i["id"]), reverse=True)[:LIMIT]


# ---------- 書き出し ----------

def rss_xml(title, desc, site_link, self_url, items):
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(title)}</title>",
        f"<link>{escape(site_link)}</link>",
        f"<description>{escape(desc)}</description>",
        "<language>ja</language>",
        f'<atom:link href={quoteattr(self_url)} rel="self" type="application/rss+xml"/>',
    ]
    if items:
        out.append(f"<lastBuildDate>{format_datetime(items[0]['date'])}</lastBuildDate>")
    for i in items:
        out += [
            "<item>",
            f"<title>{escape(i['title'])}</title>",
            f"<link>{escape(i['link'])}</link>",
            f'<guid isPermaLink="false">{escape(i["guid"])}</guid>',
            f"<pubDate>{format_datetime(i['date'])}</pubDate>",
            *[f"<category>{escape(c)}</category>" for c in dict.fromkeys(i["categories"])],
            f"<description>{escape(i['title'])}\n{escape(i['link'])}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_site(site, cfg, out, feed_base):
    """1サイト分のお知らせフィードを書き出し、一覧用の情報を返す"""
    base = cfg["base"]
    version, _ = hpwatch.fetch_version(base)
    artists = json.loads(hpwatch.get(f"{base}/json/{version}/artist_list.json"))
    profiles = artists.get("profilesById") or {}
    raw = fetch_news(base, version, recent_years())
    if not raw:
        raise RuntimeError(f"{site}: お知らせが1件も取れなかった")
    items = [to_item(site, base, x) for x in raw]

    feeds = []

    def emit(slug, name, subset):
        path = f"{site}/news/{slug}.xml"
        url = f"{feed_base}/{path}"
        write(os.path.join(out, path), rss_xml(
            f"{cfg['label']} お知らせ ― {name}", f"{base} のお知らせ（{name}）", f"{base}/news/", url, newest(subset)))
        feeds.append({"site": site, "label": cfg["label"], "kind": "お知らせ", "name": name, "url": url, "count": min(len(subset), LIMIT)})

    emit("all", "全員" if not cfg["by_group"] else "全体", items)
    if cfg["by_group"]:
        for pid, p in sorted(profiles.items(), key=lambda kv: int(kv[0])):
            if p.get("type") != "group" or not p.get("slug"):
                continue
            subset = [i for i in items if pid in i["groups"]]
            if subset:  # 記事が1件も無いグループはフィードを作らない
                emit(p["slug"], p.get("nameJa") or p["slug"], subset)
    return feeds


# ---------- 一覧ページ・OPML ----------

def opml(feeds):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">', "<head><title>hello-rss</title></head>", "<body>"]
    for f in feeds:
        t = f"{f['label']} {f['kind']} ― {f['name']}"
        out.append(f'<outline type="rss" text={quoteattr(t)} title={quoteattr(t)} xmlUrl={quoteattr(f["url"])}/>')
    out += ["</body>", "</opml>", ""]
    return "\n".join(out)


INDEX_CSS = """
:root{--bg:#fff;--fg:#222;--mute:#666;--line:#ddd;--accent:#0a66c2}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mute:#999;--line:#333;--accent:#6cb2ff}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:16px/1.7 "Noto Sans JP",system-ui,sans-serif}
main{max-width:720px;margin:0 auto}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 4px}
p,.mute{color:var(--mute);margin:4px 0}a{color:var(--accent)}ul{padding:0;list-style:none;margin:0}
li{padding:8px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
code{font-size:.8rem;color:var(--mute);word-break:break-all}
"""


def index_html(feeds, feed_base, now):
    out = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>hello-rss</title>", f"<style>{INDEX_CSS}</style></head><body><main>",
        "<h1>hello-rss</h1>",
        "<p>公式サイトの情報を RSS にしたものです（非公式）。リーダーにURLを登録して使います。</p>",
        '<p><a href="all.opml">全部まとめて登録する（OPML）</a></p>',
    ]
    for site in SITES:
        group = [f for f in feeds if f["site"] == site]
        if not group:
            continue
        out.append(f"<h2>{escape(SITES[site]['label'])} お知らせ</h2><ul>")
        for f in group:
            out.append(f'<li><a href={quoteattr(f["url"])}>{escape(f["name"])}</a><code>{escape(f["url"])}</code></li>')
        out.append("</ul>")
    out.append(f'<p class="mute">更新: {now:%Y-%m-%d %H:%M} JST</p></main></body></html>\n')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--feed-base", default="https://hello-rss.karin-archive.com")
    args = ap.parse_args()
    feed_base = args.feed_base.rstrip("/")

    feeds, failed = [], []
    for site, cfg in SITES.items():
        try:
            feeds += build_site(site, cfg, args.out, feed_base)
        except Exception as e:  # 片方のサイトが落ちても、もう片方は出す
            print(f"error: {site}: {e}", file=sys.stderr)
            failed.append(site)
    if not feeds:
        sys.exit(1)

    now = dt.datetime.now(JST)
    write(os.path.join(args.out, "index.html"), index_html(feeds, feed_base, now))
    write(os.path.join(args.out, "all.opml"), opml(feeds))
    print(f"feeds: {len(feeds)}" + (f" (失敗: {', '.join(failed)})" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
