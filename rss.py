"""公式のJSONから RSS（と一覧ページ・OPML）を作る

  python rss.py --out public [--state-dir state] [--feed-base https://hello-rss.karin-archive.com]

お知らせ（news）
  記事ID・公開日・リンクが最初からJSONに入っているので、前回の状態は持たない
  （毎回JSONだけから作り直しても、記事ID(guid)が同じなのでリーダー側で重複しない）。
  更新は追わない。載った日に1回配信して終わり。

MEDIA（media）
  スケジュールの TV / RADIO / MAGAZINE / WEB で、前回なかった予定を配信する（テレビ・ラジオはだいたい1週間前に載る）。
  毎週の番組は出さない。放送日を過ぎてから載った予定は出さない（WEB・雑誌は「掲載済」を付けて出す）。
  ラジオには radiko のタイムフリーのリンクを付ける（radiko.py）。
  状態ファイル state/rss_media_{site}.json に「見た予定」と「配信した記事」を持つ。
  初回は今日から1週間分の予定を入れて始める。

フィード（out 配下）:
  hp/news/{グループslug}.xml, hp/news/all.xml     … ハロプロのお知らせ（グループ別）
  hp/media/{グループslug}.xml, hp/media/all.xml   … ハロプロの MEDIA（グループ別）
  jproom/news/all.xml, jproom/media/all.xml       … JPROOM（所属が個人なので全員まとめて1本）
  index.html, all.opml                            … 一覧ページとまとめて登録するファイル
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from email.utils import format_datetime
from xml.sax.saxutils import escape, quoteattr

import hpwatch
import radiko

JST = dt.timezone(dt.timedelta(hours=9))
LIMIT = 50  # 1フィードに出す記事数

SITES = {
    "hp": {"label": "ハロプロ", "base": "https://helloproject.com", "by_group": True},
    "jproom": {"label": "JPROOM", "base": "https://www.jp-r.co.jp", "by_group": False},
}
KINDS = {"news": "お知らせ", "media": "MEDIA"}

MEDIA_CATS = ("TV", "RADIO", "MAGAZINE", "WEB")
MEDIA_KEEP = 300  # 状態ファイルに残す配信済みの記事数
SEEN_DAYS = 60    # 「見た予定」は放送日からこの日数で忘れる
LATE_DAYS = 30    # 放送日を過ぎて載った WEB・雑誌を「掲載済」で出す範囲
SEED_DAYS = 7     # 初回に入れる先の日数
WEEK = "月火水木金土日"


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def abs_link(base, link):
    if not link:
        return ""
    return base + link if link.startswith("/") else link


# ---------- お知らせ ----------

def fetch_news(base, version, years):
    items = []
    for y in years:
        try:
            items += json.loads(hpwatch.get(f"{base}/json/{version}/{y}_news_list.json"))["items"]
        except Exception as e:  # 年が変わった直後などで前年・今年のどちらかが無いことがある
            print(f"warn: {y}_news_list.json: {e}", file=sys.stderr)
    return items


def news_item(site, base, x):
    """タイトルは [NEWS] か [EVENT] だけ。公式の UPDATE / NEW の印は後から付いたり外れたりするので、
    タイトルには入れずカテゴリ欄にだけ残す"""
    title = hpwatch.text_of(x.get("title")).strip()
    cat = x.get("category") or "NEWS"
    # リンク欄があればそれ。無ければ /news/{id}/（NEWS の記事にだけ存在する）
    link = abs_link(base, (x.get("link") or {}).get("link")) or f"{base}/news/{x['id']}/"
    return {
        "guid": f"{site}-news-{x['id']}",
        "sort": x["id"],
        "title": f"[{cat}] {title}",
        "link": link,
        "date": dt.datetime.fromisoformat(x["date"]).replace(tzinfo=JST),
        "html": f"<p>{escape(title)}</p>",
        "categories": [cat] + ([x["flag"]] if x.get("flag") else []) + list(x.get("tags") or []),
        "groups": [str(i) for i in x.get("artistsSearch") or []],
    }


# ---------- MEDIA ----------

def media_candidates(schedules, profiles):
    """MEDIA の単発の予定（毎週の番組を除く）"""
    regulars = {hpwatch.regular_key(r) for p in profiles.values() for r in p.get("regular") or []}
    out = []
    for x in schedules:
        if x.get("category") not in MEDIA_CATS or not x.get("date"):
            continue
        kind, _ = hpwatch.ical_id(x)
        label = (x.get("schedule") or {}).get("label") or ""
        if kind == "regular" or hpwatch.regular_key(x) in regulars or re.search(hpwatch.RECURRING, label):
            continue
        out.append(x)
    return out


def media_keys(x):
    """同じ予定かどうかを見るキー。中身のハッシュと、あれば公式のID（s{id}）。
    放送日を過ぎると公式のIDが外れ、タイトルが直るとハッシュが変わるので、どちらかが合えば同じ予定とみなす"""
    s = x.get("schedule") or {}
    raw = json.dumps([x.get("date"), s.get("start"), x.get("category"),
                      hpwatch.text_of(x.get("title")).strip(), hpwatch.link_of(x)], ensure_ascii=False)
    keys = ["h" + hashlib.sha1(raw.encode()).hexdigest()[:16]]
    kind, iid = hpwatch.ical_id(x)
    if kind == "schedule":
        keys.append(f"s{iid}")
    return keys


def md(date):
    d = dt.date.fromisoformat(date)
    return f"{d.month}/{d.day}({WEEK[d.weekday()]})"


def radiko_links(x, title, start, rk):
    """ラジオのタイムフリーのリンク [(文字, URL)] と、注意書き"""
    links, notes = [], []
    ft = radiko.ft_of(x["date"], start)
    for sid in rk.station_ids(title):
        name, area = rk.name(sid), rk.free_area(sid)
        p = rk.lookup(sid, ft)
        if p is None or p.get("ts_ng"):
            notes.append(f"{name}: radiko のタイムフリーの対象外です")
            continue
        text = f"radiko タイムフリー（{name}{'・無料で聴けるのは' + area if area else ''}）"
        if p.get("corner"):
            text += f" ※番組の途中、{start}ごろから"
        links.append((text, radiko.timefree_url(sid, p["ft"])))
    if links:
        notes.append("radiko のタイムフリーは放送後1週間、無料で聴けます（地域の外はエリアフリー（有料）が必要）")
    return links, notes


def media_item(site, base, x, names, rk, now, late):
    s = x.get("schedule") or {}
    cat = x["category"]
    title = hpwatch.text_of(x.get("title")).strip()
    start = s.get("start") or ""
    tags = (["FC限定"] if x.get("isFc") else []) + ([x["status"]] if x.get("status") else []) + (["掲載済"] if late else [])
    head = " ".join(p for p in [f"[{cat}]", md(x["date"]), start, title] if p)
    head += "".join(f" [{t}]" for t in tags)

    link = abs_link(base, hpwatch.link_of(x))
    paras = []
    who = hpwatch.who({"members": x.get("membersSearch") or [], "artists": x.get("artistsSearch") or []}, names)
    if who:
        paras.append(f"出演: {who}")
    note = hpwatch.text_of(x.get("note")).strip()
    if note:
        paras.append(note)
    links = [("番組ページ", link)] if link else []
    if cat == "RADIO" and start and not x.get("status"):
        rl, notes = radiko_links(x, title, start, rk)
        links += rl
        paras += notes
    gcal = (x.get("calendar") or {}).get("google")
    if gcal and not late:
        links.append(("Googleカレンダーに追加", gcal))

    html = "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in paras)
    if links:
        html += "<ul>" + "".join(f"<li><a href={quoteattr(u)}>{escape(t)}</a></li>" for t, u in links) + "</ul>"
    return {
        "guid": f"{site}-media-{media_keys(x)[0]}",
        "sort": f"{x['date']} {start}",
        "title": head,
        "link": link or f"{base}/schedule/",
        "date": now.isoformat(),
        "html": html,
        "categories": [cat] + tags,
        "groups": [str(i) for i in x.get("artistsSearch") or []],
    }


def update_media(site, base, schedules, profiles, names, state_path, now, rk):
    """新しく載った MEDIA の予定を状態ファイルの記事に足す。戻り値: (記事, 新しい状態)"""
    state = None
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    first = state is None
    seen = dict((state or {}).get("seen") or {})
    items = list((state or {}).get("items") or [])

    today = now.date()
    seed_end = (today + dt.timedelta(days=SEED_DAYS)).isoformat()
    late_from = (today - dt.timedelta(days=LATE_DAYS)).isoformat()
    today = today.isoformat()

    new = []
    cands = media_candidates(schedules, profiles)
    for x in sorted(cands, key=lambda x: (x["date"], (x.get("schedule") or {}).get("start") or "")):
        keys = media_keys(x)
        known = any(k in seen for k in keys)
        for k in keys:  # 公式のIDとハッシュの組を覚え直す（片方が変わっても追える）
            seen[k] = x["date"]
        if known:
            continue
        late = x["date"] < today
        if first:
            if not (today <= x["date"] <= seed_end):
                continue
        elif late and not (x["category"] in ("WEB", "MAGAZINE") and x["date"] >= late_from):
            continue  # 放送日を過ぎてから載ったテレビ・ラジオは出さない
        new.append(media_item(site, base, x, names, rk, now, late))

    cutoff = (now.date() - dt.timedelta(days=SEEN_DAYS)).isoformat()
    seen = {k: d for k, d in seen.items() if d >= cutoff}
    have = {i["guid"] for i in items}
    items = [i for i in new if i["guid"] not in have] + items
    items.sort(key=lambda i: (i["date"], i["sort"]), reverse=True)
    items = items[:MEDIA_KEEP]
    print(f"{site} media: 候補{len(cands)} 新規{len(new)}{'（初回）' if first else ''}")
    return items, {"seen": seen, "items": items}


# ---------- 書き出し ----------

def as_dt(v):
    return v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(v)


def newest(items):
    return sorted(items, key=lambda i: (as_dt(i["date"]), str(i["sort"])), reverse=True)[:LIMIT]


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
        out.append(f"<lastBuildDate>{format_datetime(as_dt(items[0]['date']))}</lastBuildDate>")
    for i in items:
        out += [
            "<item>",
            f"<title>{escape(i['title'])}</title>",
            f"<link>{escape(i['link'])}</link>",
            f'<guid isPermaLink="false">{escape(i["guid"])}</guid>',
            f"<pubDate>{format_datetime(as_dt(i['date']))}</pubDate>",
            *[f"<category>{escape(c)}</category>" for c in dict.fromkeys(i["categories"])],
            f"<description>{escape(i['html'])}</description>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def emit_feeds(site, cfg, kind, items, profiles, out, feed_base, site_link):
    """全体1本と（ハロプロは）グループ別を書き出し、一覧用の情報を返す"""
    feeds = []

    def emit(slug, name, subset):
        path = f"{site}/{kind}/{slug}.xml"
        url = f"{feed_base}/{path}"
        write(os.path.join(out, path), rss_xml(
            f"{cfg['label']} {KINDS[kind]} ― {name}", f"{cfg['base']} の{KINDS[kind]}（{name}）・非公式",
            site_link, url, newest(subset)))
        feeds.append({"site": site, "kind": kind, "name": name, "url": url})

    emit("all", "全体" if cfg["by_group"] else "全員", items)
    if cfg["by_group"]:
        for pid, p in sorted(profiles.items(), key=lambda kv: int(kv[0])):
            if p.get("type") == "group" and p.get("slug"):
                emit(p["slug"], p.get("nameJa") or p["slug"], [i for i in items if pid in i["groups"]])
    return feeds


def build_site(site, cfg, out, state_dir, feed_base, now, rk):
    base = cfg["base"]
    version, years = hpwatch.fetch_version(base)
    schedules, artists = hpwatch.fetch_data(base, version, years)
    profiles = artists.get("profilesById") or {}
    names = {str(k): v.get("nameJa") or v.get("slug") for k, v in profiles.items()}

    raw = fetch_news(base, version, [str(now.year - 1), str(now.year)])
    if not raw:
        raise RuntimeError(f"{site}: お知らせが1件も取れなかった")
    if not schedules:
        raise RuntimeError(f"{site}: スケジュールが1件も取れなかった")
    news = [news_item(site, base, x) for x in raw]

    state_path = os.path.join(state_dir, f"rss_media_{site}.json")
    media, state = update_media(site, base, schedules, profiles, names, state_path, now, rk)

    feeds = emit_feeds(site, cfg, "news", news, profiles, out, feed_base, f"{base}/news/")
    feeds += emit_feeds(site, cfg, "media", media, profiles, out, feed_base, f"{base}/schedule/")

    # フィードを書けてから状態を保存する。中身が同じなら書かない（コミットが増えないように）
    text = json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True)
    old = open(state_path, encoding="utf-8").read() if os.path.exists(state_path) else None
    if text != old:
        write(state_path, text)
    return feeds


# ---------- 一覧ページ・OPML ----------

def feed_title(f):
    return f"{SITES[f['site']]['label']} {KINDS[f['kind']]} ― {f['name']}"


def opml(feeds):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">', "<head><title>hello-rss</title></head>", "<body>"]
    for f in feeds:
        t = feed_title(f)
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

KIND_NOTE = {
    "news": "公式のお知らせ（イベントの発表を含む）。載った日に配信。",
    "media": "テレビ・ラジオ・雑誌・WEB の新しく載った出演予定（だいたい1週間前）。毎週の番組は含みません。ラジオには radiko のタイムフリーのリンク付き。",
}


def index_html(feeds, now):
    out = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>hello-rss</title>", f"<style>{INDEX_CSS}</style></head><body><main>",
        "<h1>hello-rss</h1>",
        "<p>公式サイトの情報を RSS にしたものです（非公式）。リーダーにURLを登録して使います。</p>",
        '<p><a href="all.opml">全部まとめて登録する（OPML）</a></p>',
    ]
    for site in SITES:
        for kind in KINDS:
            group = [f for f in feeds if f["site"] == site and f["kind"] == kind]
            if not group:
                continue
            out.append(f"<h2>{escape(SITES[site]['label'])} {KINDS[kind]}</h2><p>{escape(KIND_NOTE[kind])}</p><ul>")
            for f in group:
                out.append(f'<li><a href={quoteattr(f["url"])}>{escape(f["name"])}</a><code>{escape(f["url"])}</code></li>')
            out.append("</ul>")
    out.append(f'<p class="mute">更新: {now:%Y-%m-%d %H:%M} JST</p></main></body></html>\n')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--feed-base", default="https://hello-rss.karin-archive.com")
    args = ap.parse_args()
    feed_base = args.feed_base.rstrip("/")
    now = dt.datetime.now(JST).replace(microsecond=0)
    rk = radiko.Radiko()

    feeds, failed = [], []
    for site, cfg in SITES.items():
        try:
            feeds += build_site(site, cfg, args.out, args.state_dir, feed_base, now, rk)
        except Exception as e:  # 片方のサイトが落ちても、もう片方は出す
            print(f"error: {site}: {e}", file=sys.stderr)
            failed.append(site)
    if not feeds:
        sys.exit(1)

    write(os.path.join(args.out, "index.html"), index_html(feeds, now))
    write(os.path.join(args.out, "all.opml"), opml(feeds))
    print(f"feeds: {len(feeds)}" + (f" (失敗: {', '.join(failed)})" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
