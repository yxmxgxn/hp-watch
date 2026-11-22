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

今日の予定（today）
  その日の予定を、毎週の番組も含めて全部、時刻順に1記事にまとめる（1日1記事、記事の時刻は朝5時）。
  同じ番組・同じ時刻で局だけ違うものは1行にまとめる。直近7日分をJSONから毎回作り直す（状態は持たない）。

ファンクラブニュース（fc）
  Hello! Project オフィシャルファンクラブ（upfc.jp/helloproject）のニュース。HTMLを読む（JSONは無い）。
  「すべて」の一覧（1ページ15件）を見て、新しく載った記事を状態ファイル state/rss_fc.json に足す。
  更新を見分ける印（Last-Modified など）が無いので、動かすたびに1ページ目を見る。記事の時刻はお知らせと同じ決め方。
  グループ別は、グループのページの一覧（news_list.php?gid=…の1ページ目）に載っているかどうかで決める（記事にはグループの印が無い）。
  フィード: 全体、ニュース（公式のタブには無い分類）、イベント、コンサート、グッズ、配信、その他、グループ別8本。

ハロショ（helloshop）
  公式の WordPress の RSS（/feed）から、記事の題名・リンク・公開時刻・カテゴリを state/rss_helloshop.json に溜める（本文・画像は持たない）。
  静的なフィードは作らず、選んで作る RSS（/custom/）の元データだけを書く。更新は時計（Cloudflare）が /feed の Last-Modified を見て知らせる。

公式の更新の記録（state/observe.jsonl）
  公式が更新される（版が変わる）たびに1行。版の時刻（日本時間）と、そのとき見つけた新着の件数。

フィード（out 配下）:
  hp/news/{グループslug}.xml, hp/news/all.xml     … ハロプロのお知らせ（グループ別）
  hp/media/{グループslug}.xml, hp/media/all.xml   … ハロプロの MEDIA（グループ別）
  fc/{all,news,event,concert,goods,stream,other}.xml, fc/group/{グループ}.xml … ファンクラブニュース
  hp/today.xml                                    … ハロプロの今日の予定（全体で1本、1日1記事）
  jproom/news/all.xml, jproom/media/all.xml, jproom/today.xml … JPROOM（所属が個人なので全員まとめて1本）
  index.html, all.opml                            … 一覧ページとまとめて登録するファイル
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import format_datetime, parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin
from xml.sax.saxutils import escape, quoteattr

import hpwatch
import radiko
import selectable

JST = dt.timezone(dt.timedelta(hours=9))
LIMIT = 50  # 1フィードに出す記事数
CAT_LABELS = {"NEWS": "お知らせ", "EVENT": "イベント", "TV": "テレビ", "RADIO": "ラジオ", "MAGAZINE": "雑誌", "WEB": "WEB"}

SITES = {
    "hp": {"label": "ハロプロ", "base": "https://helloproject.com", "by_group": True},
    "jproom": {"label": "JPROOM", "base": "https://www.jp-r.co.jp", "by_group": False},
    "upfront": {"label": "アップフロント", "base": "https://up-front-create.com", "by_group": False},
}
KINDS = {"news": "お知らせ", "media": "MEDIA", "today": "今日の予定"}

MEDIA_CATS = ("TV", "RADIO", "MAGAZINE", "WEB")
MEDIA_KEEP = 300  # 状態ファイルに残す配信済みの記事数
SEEN_DAYS = 60    # 「見た予定」は放送日からこの日数で忘れる
LATE_DAYS = 30    # 放送日を過ぎて載った WEB・雑誌を「掲載済」で出す範囲
SEED_DAYS = 7     # 初回に入れる先の日数
NEWS_LATE_DAYS = 7  # お知らせ: 公式の日付がこれより前の記事は、初めて見ても公式の日付のまま（取得に失敗した年が戻ってきたとき等）
TODAY_DAYS = 7    # 「今日の予定」に残す日数
TODAY_HOUR = 5    # 「今日の予定」の記事の時刻（radiko の1日の始まりに合わせる）
WEEK = "月火水木金土日"


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def save_state(path, state):
    """中身が同じなら書かない（コミットが増えないように）"""
    text = json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if text != old:
        write(path, text)


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


def news_item(site, base, x, pm):
    """タイトルは「2026.11.16 [NEWS] 題名」（公式サイトの並びと日付の書き方）。種類は [NEWS] か [EVENT] だけ。公式の UPDATE / NEW の印は後から付いたり外れたりするので、
    タイトルには入れずカテゴリ欄にだけ残す"""
    title = hpwatch.text_of(x.get("title")).strip()
    cat = x.get("category") or "NEWS"
    # リンク欄があればそれ。無ければ /news/{id}/（NEWS の記事にだけ存在する）
    link = abs_link(base, (x.get("link") or {}).get("link")) or f"{base}/news/{x['id']}/"
    who = selectable.who_of(x.get("artistsSearch"), pm)
    return {
        "guid": f"{site}-news-{x['id']}",
        "sort": x["id"],
        "title": f"{ymd(x['date'])} [{cat}] {title}",
        "link": link,
        # JSONには日付しか無い。0時ちょうどだと、リーダーによっては前日の23:59と表示されるので、正午にしておく
        "date": dt.datetime.fromisoformat(x["date"]).replace(hour=12, tzinfo=JST),
        "html": news_html(x, cat, link),
        "categories": [cat] + ([x["flag"]] if x.get("flag") else []) + list(x.get("tags") or []),
        "groups": [str(i) for i in x.get("artistsSearch") or []],
        "who": who,  # 選択式フィード用
        "cats": [cat],
        "name": title,  # 言葉で探すときに見る、飾りの無い題名
        "general": selectable.is_general(who, x.get("tags")),  # 特定の人・グループに紐づかない全体のもの
    }


def news_html(x, cat, link):
    """本文はタイトルの繰り返しにしない。グループ・種類・日付の1行と、公式へのリンク"""
    kind = {"NEWS": "お知らせ", "EVENT": "イベント"}.get(cat, cat)
    line = " ・ ".join(p for p in ["・".join(x.get("tags") or []), kind, ymd(x["date"])] if p)
    return f"<p>{escape(line)}</p><p><a href={quoteattr(link)}>公式サイトで読む</a></p>"


def stamp_news(items, state_path, now):
    """記事の時刻（pubDate）を決める。お知らせのJSONには日付しか無いので、こちらが初めて見た時刻を使う。
    - 初回（状態ファイルなし）: 今ある分は公式の日付の正午のまま
    - 状態に無い新しい記事: 今の時刻。ただし公式の日付が NEWS_LATE_DAYS より前なら公式の日付（正午）のまま
    - 一度決めた時刻は変えない（同じ記事の pubDate が動くと、リーダーで並びが乱れる）"""
    state = None
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    first = state is None
    seen = dict((state or {}).get("seen") or {})
    recent = now.date() - dt.timedelta(days=NEWS_LATE_DAYS)
    found = 0  # 今回はじめて見つけた記事（初回は数えない）
    for i in items:
        t = seen.get(i["guid"])
        if t:
            i["date"] = dt.datetime.fromisoformat(t)
            continue
        if not first:
            found += 1
        if not first and i["date"].date() >= recent:
            i["date"] = now
        seen[i["guid"]] = i["date"].isoformat()
    cutoff = (now - dt.timedelta(days=730)).isoformat()  # 2年より古い記事は忘れる（時刻はすべて JST なので文字列で比べられる）
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    return items, {"seen": seen}, found


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


def ymd(date):
    """公式サイトの日付の書き方（2026.11.6）"""
    d = dt.date.fromisoformat(date)
    return f"{d.year}.{d.month}.{d.day}"


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


def perf_of(x):
    """出演は公式のタグに従う（HELLO! PROJECT ／ BEYOOOOONDS ／ BEYOOOOONDS：平井美葉 など）"""
    return list(x.get("tags") or [])


def media_who(x, pm):
    """グループ（artistsSearch）と、出るメンバー（membersSearch）のキー"""
    return selectable.who_of(list(x.get("artistsSearch") or []) + list(x.get("membersSearch") or []), pm)


def media_item(site, base, x, rk, now, late, pm):
    s = x.get("schedule") or {}
    cat = x["category"]
    title = hpwatch.text_of(x.get("title")).strip()
    start = s.get("start") or ""
    tags = (["FC限定"] if x.get("isFc") else []) + ([x["status"]] if x.get("status") else []) + (["掲載済"] if late else [])
    head = " ".join(p for p in [f"[{cat}]", md(x["date"]), start, title] if p)
    head += "".join(f" [{t}]" for t in tags)

    link = abs_link(base, hpwatch.link_of(x))
    paras = []
    perf = perf_of(x)
    if perf:
        paras.append(f"出演: {'、'.join(perf)}")
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
        "categories": [cat] + tags + perf,
        "groups": [str(i) for i in x.get("artistsSearch") or []],
        "who": media_who(x, pm),  # 選択式フィード用
        "cats": [cat],
        "name": title,
        "general": selectable.is_general(media_who(x, pm), x.get("tags")),
    }


def update_media(site, base, schedules, profiles, state_path, now, rk):
    """新しく載った MEDIA の予定を状態ファイルの記事に足す。戻り値: (記事, 新しい状態)"""
    state = None
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    first = state is None
    seen = dict((state or {}).get("seen") or {})
    items = list((state or {}).get("items") or [])
    pm = selectable.people_map(profiles)

    # 選択式フィード用の「人・グループ」「カテゴリ」を持たない、これまでの記事に付ける（公式のJSONに残っているものだけ。
    # 無いものは、グループだけ付ける）
    who_by_guid = {f"{site}-media-{media_keys(x)[0]}": media_who(x, pm) for x in media_candidates(schedules, profiles)}
    for i in items:
        if "who" not in i:
            i["who"] = who_by_guid.get(i["guid"]) or selectable.who_of(i.get("groups"), pm)
        if "cats" not in i:
            i["cats"] = i["categories"][:1]
        if "general" not in i:
            i["general"] = selectable.is_general(i["who"], i.get("categories"))
        if "name" not in i:
            i["name"] = selectable.plain_title(i["title"])

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
        new.append(media_item(site, base, x, rk, now, late, pm))

    cutoff = (now.date() - dt.timedelta(days=SEEN_DAYS)).isoformat()
    seen = {k: d for k, d in seen.items() if d >= cutoff}
    have = {i["guid"] for i in items}
    items = [i for i in new if i["guid"] not in have] + items
    items.sort(key=lambda i: (i["date"], i["sort"]), reverse=True)
    items = items[:MEDIA_KEEP]
    print(f"{site} media: 候補{len(cands)} 新規{len(new)}{'（初回）' if first else ''}")
    return items, {"seen": seen, "items": items}, (0 if first else len(new))


# ---------- 今日の予定 ----------

def split_station(cat, title):
    """「ラジオ日本「番組名」」→ ("ラジオ日本", "番組名")。テレビ・ラジオ・WEB 以外や「」が無いものは局なし"""
    if cat in ("TV", "RADIO", "WEB") and "「" in title and title.endswith("」"):
        head, prog = title.split("「", 1)
        return head.strip(), prog[:-1]
    return "", title


def time_key(start):
    if not start:
        return -1  # 時刻なし（雑誌・発売日・誕生日など）は先頭
    h, m = map(int, start.split(":"))
    return h * 60 + m


def today_rows(xs):
    """その日の予定を行にする。同じ番組・同じ時刻・同じ出演者で局だけ違うものは1行にまとめる"""
    rows = {}
    for x in xs:
        s = x.get("schedule") or {}
        cat = x.get("category") or ""
        title = hpwatch.text_of(x.get("title")).strip()
        station, prog = split_station(cat, title)
        start = s.get("start") or ""
        who = "、".join(perf_of(x))
        key = (start, cat, prog, who, x.get("status") or "", bool(x.get("isFc")))
        r = rows.setdefault(key, {"start": start, "cat": cat, "prog": prog, "who": who, "status": x.get("status"),
                                  "fc": bool(x.get("isFc")), "stations": [], "items": []})
        if station and station not in r["stations"]:
            r["stations"].append(station)
        r["items"].append(x)
    return sorted(rows.values(), key=lambda r: (time_key(r["start"]), r["cat"], r["prog"]))


def today_row_html(r, base, rk):
    x = r["items"][0]
    link = abs_link(base, hpwatch.link_of(x))
    prog = r["prog"]
    if r["stations"] and ("「" in prog or "」" in prog):  # 「番組」内「コーナー」は、かぎかっこごと出す
        prog = f"「{prog}」"
    name = f"<a href={quoteattr(link)}>{escape(prog)}</a>" if link else escape(prog)
    head = f"<b>{escape(r['start'] or '—')}</b> [{escape(r['cat'])}] {name}"
    if r["stations"]:
        head += f"（{escape(' / '.join(r['stations']))}）"
    tags = (["FC限定"] if r["fc"] else []) + ([r["status"]] if r["status"] else [])
    head += "".join(f" [{escape(t)}]" for t in tags)
    lines = [head]
    if r["who"]:
        lines.append(f"出演: {escape(r['who'])}")
    if r["cat"] == "RADIO" and r["start"] and not r["status"]:
        ft = radiko.ft_of(x["date"], r["start"])
        rl = []
        for it in r["items"]:
            for sid in rk.station_ids(hpwatch.text_of(it.get("title")).strip()):
                p = rk.lookup(sid, ft)
                if p and not p.get("ts_ng"):
                    rl.append(f"<a href={quoteattr(radiko.timefree_url(sid, p['ft']))}>radiko（{escape(rk.name(sid))}）</a>")
        if rl:
            lines.append(" ".join(dict.fromkeys(rl)))
    return "<li>" + "<br>".join(lines) + "</li>"


def today_items(site, base, schedules, now, rk):
    """直近 TODAY_DAYS 日の「今日の予定」。JSON から毎回作り直す（過去の日の予定もJSONに残っている）"""
    by_date = {}
    for x in schedules:
        if x.get("date"):
            by_date.setdefault(x["date"], []).append(x)
    out = []
    for i in range(TODAY_DAYS):
        day = now.date() - dt.timedelta(days=i)
        d = day.isoformat()
        rows = today_rows(by_date.get(d) or [])
        if not rows:
            continue
        html = ("<ul>" + "".join(today_row_html(r, base, rk) for r in rows) + "</ul>"
                "<p>ラジオの radiko のリンクはタイムフリー（放送後1週間）。放送中は radiko の生放送で聴けます。</p>")
        cats = sorted({r["cat"] for r in rows if r["cat"]})
        out.append({
            "guid": f"{site}-today-{d}",
            "sort": d,
            "title": f"{md(d)}の予定（{len(rows)}件）",
            "link": f"{base}/schedule/",
            "date": dt.datetime.combine(day, dt.time(TODAY_HOUR), JST),
            "html": html,
            "categories": cats,
            "groups": [],
        })
    return out


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
    stamp = now.replace(minute=0, second=0)  # 記事の時刻（こちらが見つけた時刻）。分・秒は0にそろえる
    version, years = hpwatch.fetch_version(base)
    schedules, artists = hpwatch.fetch_data(base, version, years)
    profiles = artists.get("profilesById") or {}

    raw = fetch_news(base, version, [str(now.year - 1), str(now.year)])
    if not raw:
        raise RuntimeError(f"{site}: お知らせが1件も取れなかった")
    if not schedules:
        raise RuntimeError(f"{site}: スケジュールが1件も取れなかった")
    pm = selectable.people_map(profiles)
    news, news_state, news_found = stamp_news([news_item(site, base, x, pm) for x in raw],
                                  os.path.join(state_dir, f"rss_news_{site}.json"), stamp)

    state_path = os.path.join(state_dir, f"rss_media_{site}.json")
    media, state, media_found = update_media(site, base, schedules, profiles, state_path, stamp, rk)

    feeds = emit_feeds(site, cfg, "news", news, profiles, out, feed_base, f"{base}/news/")
    feeds += emit_feeds(site, cfg, "media", media, profiles, out, feed_base, base + cfg.get("schedule_path", "/schedule/"))

    path = f"{site}/today.xml"
    url = f"{feed_base}/{path}"
    name = "全体" if cfg["by_group"] else "全員"
    write(os.path.join(out, path), rss_xml(
        f"{cfg['label']} 今日の予定", f"{base} のスケジュールから、その日の予定を毎朝1本にまとめたもの・非公式",
        base + cfg.get("schedule_path", "/schedule/"), url, newest(today_items(site, base, schedules, now, rk))))
    feeds.append({"site": site, "kind": "today", "name": name, "url": url})

    # 選択式フィードの元データ（人・グループは、プロフィールの順。種別: group・unit はグループ、member・solo は人）
    who_info = list(pm.values())
    # アップフロントは、ハロプロ以外の人（歌手・俳優など）も所属しているので、「人」の選択肢は、ハロプロ関係の人の名簿（hp_people.txt）の人に絞る。
    # 記事のデータは絞らない（名簿に無い人の記事も、全部・言葉の検索には出る）
    roster = selectable.load_roster(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hp_people.txt")) if site == "upfront" else None
    keep = (lambda n: selectable.compact_name(n) in roster) if roster else None
    sources = [
        selectable.make_source(out, site, cfg["label"], "news", "お知らせ", news, who_info,
                               [(k, CAT_LABELS[k]) for k in ("NEWS", "EVENT")], who_keep=keep),
        selectable.make_source(out, site, cfg["label"], "schedule", "スケジュール（新しく載った出演）", media, who_info,
                               [(k, CAT_LABELS[k]) for k in MEDIA_CATS], who_all=True, who_keep=keep),
    ]

    if site == "hp":
        sources.append(ameblo_source(profiles, pm))

    # フィードを書けてから状態を保存する
    save_state(state_path, state)
    save_state(os.path.join(state_dir, f"rss_news_{site}.json"), news_state)
    return feeds, {"site": site, "version": version, "news": news_found, "media": media_found}, sources


# ---------- ファンクラブニュース ----------

FC_BASE = "https://www.upfc.jp"
FC_KEEP = 300   # 状態ファイルに残す記事数
FC_PAGES = 3    # 「すべて」を1回に見るページ数の上限（1ページ15件）
FC_ITEM_RE = re.compile(
    r'<li>\s*<a href="([^"]+)">\s*<p class="news__date[^"]*">\s*([\d.]+)\s*<span class="news__ctg">([^<]*)</span>\s*</p>'
    r'\s*<p class="news__txt[^"]*">(.*?)</p>', re.S)

# ファンクラブごとの設定。cats は公式のタブの順（ニュースはタブには無いが「すべて」の中にある分類）。
# who はグループ別（フィード名・ファンクラブのページの uid・名前）。None なら、ファンクラブのトップのページにあるメンバーの一覧から読む
FC_SITES = {
    "fc": {
        "label": "ハロプロFC", "title": "ファンクラブ ニュース", "path": "/helloproject",
        "note": "Hello! Project オフィシャルファンクラブのニュース。公式のタブどおりに分けています（ニュースだけは、タブには無い分類です）。",
        "cats": [("news", "ニュース"), ("event", "イベント"), ("concert", "コンサート"), ("goods", "グッズ"), ("stream", "配信"), ("other", "その他")],
        "who": [("morningmusume", "MORNING", "モーニング娘。'26"), ("angerme", "ANGERME", "アンジュルム"),
                ("juicejuice", "JUICEJUICE", "Juice=Juice"), ("tsubakifactory", "TSUBAKIFACTORY", "つばきファクトリー"),
                ("beyooooonds", "BEYOOOOONDS", "BEYOOOOONDS"), ("ochanorma", "OCHANORMA", "OCHA NORMA"),
                ("rosychronicle", "ROSYCHRONICLE", "ロージークロニクル"), ("helloprokenshusei", "KENSYUSEI", "ハロプロ研修生")],
        "who_type": "group", "static_who": True,  # グループ別のフィードも作る
    },
    "mline": {
        "label": "M-line club FC", "title": "M-line club ファンクラブ ニュース", "path": "/m-line",
        "note": "M-line club オフィシャルファンクラブのニュース。公式のタブどおりに分けています（ニュースだけは、タブには無い分類です）。人ごとに見たいときは「好きな人・グループ・カテゴリだけの RSS」で選べます。",
        "cats": [("news", "ニュース"), ("fcevent", "FCイベント"), ("ticket", "チケット"), ("goods", "グッズ"), ("contents", "コンテンツ"), ("other", "その他")],
        "who": None,
        "who_type": "member", "static_who": False,  # 29人分の静的なフィードは作らない（選択式で選ぶ）
    },
}


def fc_get(url):
    time.sleep(0.3)  # 続けて取りに行かない
    return hpwatch.get(url)


def fc_parse(page, url, prefix):
    """一覧（news_list.php。「すべて」の ctg00）の記事。上から新しい順。1件も読めなければ、作りが変わったとみなして失敗にする"""
    m = re.search(r'<div class="filter-atc news_list_wrap[^"]*" data-category="ctg00">(.*?)(?=<div class="filter-atc news_list_wrap|\Z)', page, re.S)
    rows = []
    for href, d, cat, title in FC_ITEM_RE.findall(m.group(1) if m else ""):
        title = unescape(re.sub(r"<[^>]+>", "", title)).strip()
        link = urljoin(url, unescape(href).strip())
        y, mo, da = (int(x) for x in d.split("."))
        # 記事ごとのIDがあるのは news_detail だけ。ほかは同じページを指す記事が複数あるので、中身から作る
        uid = re.search(r"news_detail\.php\?@uid=(\w+)", link)
        key = uid.group(1) if uid else "h" + hashlib.sha1(f"{d}|{cat}|{title}|{link}".encode()).hexdigest()[:12]
        rows.append({"guid": f"{prefix}-{key}", "d": d, "jd": dt.date(y, mo, da), "cat": cat.strip(), "title": title, "link": link})
    if not rows:
        raise RuntimeError(f"ファンクラブニュース: 記事が1件も読めない（サイトの作りが変わった？）{url}")
    return rows


def fc_item(r, idx, when, groups):
    return {
        "guid": r["guid"],
        "sort": f"{r['jd'].isoformat()}{99 - idx:02d}",  # 同じ日・同じ時刻の中は、ページの上にあるほうが新しい
        "title": f"{r['d']} [{r['cat']}] {r['title']}",
        "link": r["link"],
        "date": when.isoformat(),
        "html": f"<p>{escape(r['cat'])} ・ {escape(r['d'])}</p><p><a href={quoteattr(r['link'])}>ファンクラブサイトで読む</a></p>",
        "categories": [r["cat"]],
        "cat": r["cat"],
        "name": r["title"],
        "fcgroups": sorted(groups),  # 載っているグループ／人の一覧のキー
    }


def fc_people(cfg, state):
    """グループ／人の [(キー, ファンクラブのページの uid, 名前)]。設定に無ければ、トップのページから読む（読めなければ前回の分）"""
    if cfg["who"] is not None:
        return cfg["who"]
    try:
        top = fc_get(FC_BASE + cfg["path"] + "/")
        out, seen = [], set()
        for uid, x in re.findall(r'<a href="[^"]*artist/\?@uid=([A-Za-z0-9_-]+)[^"]*"[^>]*>(.*?)</a>', top, re.S):
            name = unescape(re.sub(r"<[^>]+>|\s+", " ", x)).strip()
            if name and uid not in seen:
                seen.add(uid)
                out.append((uid.lower(), uid, name))
        if not out:
            raise RuntimeError("メンバーの一覧が読めない")
        return out
    except Exception as e:
        if state and state.get("people"):
            print(f"warn: fc {cfg['path']}: {e}（前回のメンバーの一覧を使う）", file=sys.stderr)
            return [tuple(p) for p in state["people"]]
        raise


def update_fc(state, stamp, site, cfg):
    """一覧を見て、新しく載った記事を足す。戻り値: (記事, 新しい状態, 新しく見つけた件数)"""
    first = state is None
    items = list((state or {}).get("items") or [])
    gids = dict((state or {}).get("gids") or {})
    by_guid = {i["guid"]: i for i in items}
    list_url = FC_BASE + cfg["path"] + "/news_list.php"
    people = fc_people(cfg, state)

    # 「すべて」。初回は2ページ分で始める。ふだんは、見たことのある記事に当たったら、それより先は新しくない
    fetched = []
    for pg in range(1, FC_PAGES + 1):
        url = list_url + ("?@rst=all" if pg == 1 else f"?@pg={pg}&@tbl=news")
        rows = fc_parse(fc_get(url), url, site)
        fetched += [(r, len(fetched) + n) for n, r in enumerate(rows)]
        if (first and pg >= 2) or (not first and any(r["guid"] in by_guid for r in rows)):
            break
    in_all = {r["guid"] for r, _ in fetched}

    # グループ別・人別（1ページ目だけ）。記事そのものにはグループ・人の印が無いので、どの一覧に載っているかで決める。
    # 2ページ目以降は、絞り込みがセッションに依るので見ない。1つが失敗しても、ほかは続ける
    member = {}
    for slug, uid, _ in people:
        try:
            if slug not in gids:  # 番号（gid）は、そのページの「view more」のリンクにある
                m = re.search(r"news_list\.php\?gid=(\d+)", fc_get(f"{FC_BASE}{cfg['path']}/artist/?@uid={uid}&@rst=all"))
                if not m:
                    raise RuntimeError("番号が見つからない")
                gids[slug] = int(m.group(1))
            url = f"{list_url}?gid={gids[slug]}&@rst=all"
            rows = fc_parse(fc_get(url), url, site)
        except Exception as e:
            print(f"warn: {site} {slug}: {e}", file=sys.stderr)
            continue
        for pos, r in enumerate(rows):
            member.setdefault(r["guid"], set()).add(slug)
            if r["guid"] not in in_all:  # 「すべて」の範囲より古いが、その一覧にはある記事
                in_all.add(r["guid"])
                fetched.append((r, 50 + pos))

    for i in items:  # 既に持っている記事に、グループ／人を足す
        g = member.get(i["guid"])
        if g:
            i["fcgroups"] = sorted(set(i.get("fcgroups") or []) | g)

    recent = stamp.date() - dt.timedelta(days=NEWS_LATE_DAYS)
    new = []
    for r, idx in fetched:
        if r["guid"] in by_guid:
            continue
        noon = dt.datetime.combine(r["jd"], dt.time(12), JST)
        # 初回と、公式の日付が古い記事は、日付の正午のまま。新しい記事は、見つけた時刻（分・秒は0）
        it = fc_item(r, idx, noon if first or r["jd"] < recent else stamp, member.get(r["guid"], set()))
        by_guid[r["guid"]] = it
        new.append(it)
    items = sorted(new + items, key=lambda i: (as_dt(i["date"]), str(i["sort"])), reverse=True)[:FC_KEEP]
    return items, {"items": items, "gids": gids, "people": [list(p) for p in people]}, 0 if first else len(new)


def build_fc(out, state_dir, feed_base, stamp, site):
    """ファンクラブニュースのフィードを書き出す。取得に失敗しても、前回の状態があればそれで書く（他のフィードの公開を止めない）"""
    cfg = FC_SITES[site]
    path = os.path.join(state_dir, "rss_fc.json" if site == "fc" else f"rss_fc_{site}.json")
    state = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    try:
        items, new_state, found = update_fc(state, stamp, site, cfg)
    except Exception as e:
        if state is None:
            raise
        print(f"warn: {site}: {e}（前回の状態で書く）", file=sys.stderr)
        items, new_state, found = state["items"], None, 0
    people = [tuple(p) for p in (new_state or state).get("people") or cfg["who"] or []]
    list_url = FC_BASE + cfg["path"] + "/news_list.php"
    slug_of = {label: slug for slug, label in cfg["cats"]}

    feeds = []

    def emit(rel, name, part, subset):
        url = f"{feed_base}/{rel}"
        write(os.path.join(out, rel), rss_xml(
            f"{cfg['title']} ― {name}", f"{FC_BASE}{cfg['path']}/ のファンクラブニュース（{name}）・非公式",
            list_url + "?@rst=all", url, newest(subset)))
        feeds.append({"site": site, "kind": "news", "name": name, "url": url, "part": part})

    emit(f"{site}/all.xml", "全体", "category", items)
    for slug, name in cfg["cats"]:
        emit(f"{site}/{slug}.xml", name, "category", [i for i in items if slug_of.get(i["cat"]) == slug])
    if cfg["static_who"]:
        for slug, _, name in people:
            emit(f"{site}/group/{slug}.xml", name, "group", [i for i in items if slug in (i.get("fcgroups") or [])])
    if new_state is not None:
        save_state(path, new_state)
    print(f"{site} news: 記事{len(items)} 新規{found}{'（初回）' if state is None else ''}")
    rows = [dict(i, who=i.get("fcgroups") or [], cats=[slug_of[i["cat"]]] if i["cat"] in slug_of else [],
                 general=not i.get("fcgroups")) for i in items]
    source = selectable.make_source(out, site, cfg["label"], "news", "ニュース", rows,
                                    [(slug, name, cfg["who_type"]) for slug, _, name in people], cfg["cats"], who_all=True)
    return feeds, [source]


# ---------- ハロショ（helloshop.info） ----------
# 公式が WordPress で RSS（/feed）を出している。記事（題名・リンク・公開時刻・カテゴリ）を、状態ファイルに溜めていく。
# 本文・画像は持たない（公式のRSSに入っているが、読まない）。更新の確認は時計（Cloudflare）が /feed の Last-Modified を見る。
# 取りに行くときは、前回の ETag を付ける（変わっていなければ 304 で、中身は送られない）

SHOP_BASE = "https://www.helloshop.info"
SHOP_KEEP = 300   # 状態ファイルに残す記事数
SHOP_PAGES = 3    # 初回に見るページ数（1ページ10件。約30日分）。ふだんは1ページ目だけ（全部新しければ次のページも）
# タグ（グループ。公式の /tag/{ID}/）とカテゴリ（公式の /category/{ID}/）。新しいものが増えたら、ここに足す
SHOP_WHO = [("morningmusume", "モーニング娘。'26"), ("angerme", "アンジュルム"), ("juicejuice", "Juice=Juice"),
            ("tsubakifactory", "つばきファクトリー"), ("beyooooonds", "BEYOOOOONDS"), ("ocha-norma", "OCHA NORMA"),
            ("rosychronicle", "ロージークロニクル"), ("sotsugyo", "ハロプロ卒業メンバー"), ("kensyu", "ハロプロ研修生"), ("other", "その他")]
SHOP_CATS = [("news", "お知らせ"), ("event", "イベント"), ("goods", "グッズ情報"), ("photo", "生写真情報"),
             ("shop", "ショップ情報"), ("shops", "店舗"), ("extra", "臨時店")]


def shop_get(url, etag=None):
    """戻り値: (本文, ETag)。前回から変わっていなければ (None, 前回のETag)。
    接続が途中で切れたとき（一時的なことが多い）は、少し待って3回まで試す。404・500 など、公式が返したエラーは、すぐ失敗にする"""
    headers = {"User-Agent": hpwatch.UA}
    if etag:
        headers["If-None-Match"] = etag
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read().decode("utf-8"), r.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return None, etag
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if i == 2:
                raise
            time.sleep(3 * (i + 1))


def shop_parse(xml_text):
    who_of = {label: key for key, label in SHOP_WHO}
    cat_of = {label: key for key, label in SHOP_CATS}
    rows = []
    for it in ET.fromstring(xml_text).find("channel").findall("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        pub = it.findtext("pubDate")
        if not (title and pub):
            continue
        labels = [c.text.strip() for c in it.findall("category") if c.text]
        who = [who_of[x] for x in labels if x in who_of]
        pid = re.search(r"[?&]p=(\d+)", guid)
        rows.append({
            "guid": "shop-" + (pid.group(1) if pid else hashlib.sha1(guid.encode()).hexdigest()[:12]),
            "sort": parsedate_to_datetime(pub).astimezone(JST).isoformat(),
            "title": title, "name": title, "link": link, "html": "",
            "date": parsedate_to_datetime(pub).astimezone(JST).isoformat(),
            "categories": labels,  # 公式のカテゴリ・タグの名前をそのまま
            "who": who, "cats": [cat_of[x] for x in labels if x in cat_of],
            "general": not who or set(who) <= {"other"},  # グループのタグが無い（店舗・お知らせなど）
        })
    return rows


def update_wp(state, feed, parse, pages, keep=SHOP_KEEP, reseed=False):
    """WordPress の公式のRSS（ハロショ・ジャストプロ）を見て、新しい記事を足す。戻り値: (記事, 新しい状態, 新しく見つけた件数)
    parse(本文) は、対象の記事の行（rows）と、見た記事の全部の guid（seen。対象外も含む）を返す。
    reseed: 溜めた記事は残したまま、初回と同じように、最初のページから pages ページ分を取り直す（対象を増やしたとき、さかのぼって拾うため）。
    対象外の記事が多いサイトでも、「前に見た記事に着いたら、次のページを見ない」が効くように、見た guid も状態に持つ"""
    first = state is None or reseed
    items = list((state or {}).get("items") or [])
    seen = [] if reseed else list((state or {}).get("seen") or [])
    etag = None if reseed else (state or {}).get("etag")
    have = {i["guid"] for i in items} | set(seen)
    fetched, new_etag, all_seen = [], etag, []
    for pg in range(1, pages + 1):
        body, e = shop_get(feed + (f"?paged={pg}" if pg > 1 else ""), None if first or pg > 1 else etag)
        if pg == 1:
            new_etag = e
        if body is None:  # 前回から変わっていない
            break
        rows, guids = parse(body)
        fetched += rows
        all_seen += guids
        if (first and pg >= pages) or (not first and any(g in have for g in guids)):
            break
        time.sleep(0.5)
    new = []
    stored = {i["guid"] for i in items}
    for r in fetched:
        if r["guid"] not in stored:
            stored.add(r["guid"])
            new.append(r)
    items = sorted(new + items, key=lambda i: (i["date"], i["guid"]), reverse=True)[:keep]
    seen = list(dict.fromkeys(all_seen + seen))[:100]
    state_out = {"items": items, "etag": new_etag}
    if seen and any(g not in stored for g in seen):  # 対象外の記事を見たサイトだけ、見た記事の記録を持つ
        state_out["seen"] = seen
    return items, state_out, 0 if first else len(new)


def update_shop(state):
    return update_wp(state, SHOP_BASE + "/feed", lambda t: (lambda r: (r, [x["guid"] for x in r]))(shop_parse(t)), SHOP_PAGES)


def build_shop(out, state_dir, now):
    """ハロショの選択式フィードの元データを書く。取得に失敗しても、前回の状態があればそれで書く"""
    path = os.path.join(state_dir, "rss_helloshop.json")
    state = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    try:
        items, new_state, found = update_shop(state)
    except Exception as e:
        if state is None:
            raise
        print(f"warn: helloshop: {e}（前回の状態で書く）", file=sys.stderr)
        items, new_state, found = state["items"], None, 0
    source = selectable.make_source(
        out, "helloshop", "ハロショ", "news", "新着情報（タイトルだけ）", items,
        [(k, n, "group") for k, n in SHOP_WHO], SHOP_CATS, who_all=True,
        note="ハロショの公式のRSSから溜めた記事の、タイトルだけを返します（本文・画像は返しません）。最初は直近30日分から始まります。")
    if new_state is not None:
        save_state(path, new_state)
    print(f"helloshop news: 記事{len(items)} 新規{found}{'（初回）' if state is None else ''}")
    return [source]


# ---------- アメブロ（ハロプロのグループ・メンバーのブログ） ----------
# 保存しない。リーダーが取りに来たときに、Worker（watcher/src/ameblo.js）が選んだブログのRSSを取りに行って、題名だけにして返す。
# ここでは、選ぶ画面と Worker に渡す選択肢（グループ・メンバーと、そのブログのID）だけを作る。
# ブログのIDは、公式のプロフィールの blogSlug（グループ: そのグループのブログ全部／メンバー: その人のブログ）。手で持つ対応表は無い。
# ブログ全体を見たいだけなら、公式のRSS（https://rssblog.ameba.jp/{ブログのID}/rss20.xml）をそのまま使うほうが確実なので、そのURLを一覧に載せる。
# JPROOM・アップフロントは、1人に1つのブログなので、対象にしない（ブログ全体＝その人の公式のRSS）。
# 人で絞れるのは、グループのブログに複数のメンバーの記事が混ざっていて、テーマ（カテゴリ）でも分かれていないハロプロだけ

def ameblo_source(profiles, pm):
    groups, persons = [], []
    for pid in sorted(profiles, key=lambda k: int(k)):
        p = profiles[pid]
        key, name, typ = pm[str(pid)]
        blogs = [b for b in (p.get("blogSlug") or []) if b]
        if not blogs or not name.strip():
            continue
        row = {"key": key, "label": name, "blogs": blogs}
        if typ == "group":
            groups.append(dict(row, type="group"))
        elif typ == "member":
            persons.append(dict(row, type="person"))
    return {
        "site": "ameblo", "siteLabel": "アメブロ（ハロプロ）", "kind": "blog", "kindLabel": "ブログ（題名だけ）",
        "live": {"type": "ameblo"}, "general": False, "who": groups + persons, "cat": [],
        "note": "ハロプロのグループ・メンバーのブログから、題名だけを返します（本文は返しません）。グループは、そのグループのブログの記事全部。"
                "メンバーは、そのメンバーのブログのうち、題名にその名前がある記事です（名前を書かない記事は拾えません）。"
                "ブログ全体を見たいときは、トップの一覧にある公式のRSSを使ってください。",
    }


# ---------- YouTube（公式のチャンネル。保存しない・リーダーが取りに来たときに取りに行く） ----------
# 公式の一覧（https://helloproject.com/link/）にあるチャンネルと、JPRTube・decarbo46。個人のチャンネルは、今の事務所に所属している人だけ。
# RSS: https://www.youtube.com/feeds/videos.xml?channel_id=UC…（最新15件・公式のもの）。チャンネルのIDは、チャンネルのページの
# RSSの印（<link rel="alternate" type="application/rss+xml">）から1回だけ引いて、state/youtube_channels.json に残す（毎回は引かない）。
# 動画とShortsは、リンクの形（/shorts/）で見分ける。保存しないので、本文の説明は持たない。埋め込み（embed=1）は、公式の埋め込みプレーヤー

YT_CHANNELS = [
    # (key, チャンネルのURL[, 種別: 省略は group（チャンネル）／person（個人）])  名前は、ページから引く
    ("morningmusume", "https://www.youtube.com/user/morningmusumechannel"),
    ("angerme", "https://www.youtube.com/c/angerme"),
    ("juicejuice", "https://www.youtube.com/user/juicejuicechannel"),
    ("tsubakifactory", "https://www.youtube.com/c/tsubakifactory"),
    ("hapisugo", "https://www.youtube.com/c/hapisugo"),
    ("beyooooonds", "https://www.youtube.com/c/beyooooonds"),
    ("beyonobi", "https://www.youtube.com/c/beyonobi"),
    ("ochanorma", "https://www.youtube.com/c/ochanorma"),
    ("rosychronicle", "https://www.youtube.com/@rosychronicle"),
    ("kenshusei", "https://www.youtube.com/user/helloprokenshusei"),
    ("helloprojectstation", "https://www.youtube.com/helloprojectstation"),
    ("mlinemusic", "https://www.youtube.com/c/mlinemusic"),
    ("upfrontchannel", "https://www.youtube.com/@upfrontchannel"),
    ("satoyama", "https://www.youtube.com/user/satoyamachannel"),
    ("omake", "https://www.youtube.com/@omake"),
    ("kimitsuta", "https://www.youtube.com/@kimitsuta"),
    ("uffanclub", "https://www.youtube.com/@UFfanclub"),
    ("jprtube", "https://www.youtube.com/@JPR_Tube"),
    ("decarbo46", "https://www.youtube.com/@decarbo46"),
    # 個人（今の事務所に所属している人だけ。アップフロントクリエイト・JPROOM・ジャストプロ）。画面では「メンバー・個人」に入る
    ("takahashi_ai", "https://www.youtube.com/@lab3546", "person"),
    ("suzuki_airi", "https://www.youtube.com/@airisuzukich", "person"),
    ("rina_katsuta", "https://www.youtube.com/@rinakatsuta_official", "person"),
    ("miyamoto_karin", "https://www.youtube.com/@miyamotokarin", "person"),
    ("mai_ozeki", "https://www.youtube.com/@ozeki_mai", "person"),
    ("miki_fujimoto", "https://www.youtube.com/@hello_mikitty", "person"),
]
YT_ID_RE = re.compile(r'<link rel="alternate" type="application/rss\+xml"[^>]*channel_id=(UC[\w-]{22})')
YT_ID_RE2 = re.compile(r'"externalId":"(UC[\w-]{22})"')
YT_NAME_RE = re.compile(r"<title>([^<]*?)(?: - YouTube)?</title>")


def youtube_resolve(url):
    """チャンネルのページから (チャンネルのID, 名前) を引く。引けなければ None"""
    req = urllib.request.Request(url, headers={"User-Agent": hpwatch.UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = YT_ID_RE.search(html) or YT_ID_RE2.search(html)
    n = YT_NAME_RE.search(html)
    if not m:
        return None
    return m.group(1), unescape(n.group(1)).strip() if n else url


def youtube_source(state_dir):
    path = os.path.join(state_dir, "youtube_channels.json")
    known = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            known = json.load(f)
    changed, who = False, []
    for key, url, *typ in YT_CHANNELS:
        rec = known.get(key)
        if not rec or rec.get("url") != url:
            try:
                got = youtube_resolve(url)
            except (urllib.error.URLError, OSError) as e:
                got = None
                print(f"YouTube: {key} のチャンネルのIDを引けませんでした（{e}）", file=sys.stderr)
            if not got:
                continue
            rec = known[key] = {"url": url, "id": got[0], "name": got[1]}
            changed = True
        who.append({"key": key, "label": rec["name"], "type": typ[0] if typ else "group", "channels": [rec["id"]], "url": rec["url"]})
        time.sleep(0.5)
    if changed:
        save_state(path, known)
    if not who:
        return None
    return {
        "site": "youtube", "siteLabel": "YouTube（ハロプロ公式）", "kind": "video", "kindLabel": "動画",
        "live": {"type": "youtube"}, "general": False, "embed": True, "who": who,
        "cat": [{"key": "video", "label": "動画"}, {"key": "shorts", "label": "Shorts"}],
        "note": "ハロプロ公式のYouTubeチャンネル（グループ・ハロ！ステ・M-line Music・JPRTubeなど）の新しい動画の題名を返します。"
                "動画とShortsは分けて選べます。保存はしません。最新15件までなので、チャンネルによっては数日分です。"
                "「動画を埋め込む」を選ぶと、公式の埋め込みプレーヤーを記事に入れます（リーダーによっては表示されません）。",
    }


# ---------- 公式の更新の記録 ----------

def version_time(version):
    """版の名前 2611191300-xxxx は日本時間の年月日時分（サイトを作り直した時刻）"""
    try:
        return dt.datetime.strptime(version[:10], "%y%m%d%H%M").replace(tzinfo=JST).isoformat()
    except ValueError:
        return None


def log_versions(path, records, now):
    """公式が更新されるたびに1行。いつ更新されて、そのとき何件の新着があったか（取りに行く時間帯を決めるための記録）。
    版が前回の記録と同じなら書かない（更新の無い回で行が増えないように）"""
    last = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    last[r["site"]] = r["version"]
                except (ValueError, KeyError):
                    pass
    lines = []
    for r in records:
        if last.get(r["site"]) == r["version"]:
            continue
        lines.append(json.dumps({"site": r["site"], "version": r["version"], "versionTime": version_time(r["version"]),
                                 "runAt": now.isoformat(), "news": r["news"], "media": r["media"]}, ensure_ascii=False))
    if lines:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ---------- 一覧ページ・OPML ----------

def feed_title(f):
    if f["site"] in FC_SITES:
        return f"{FC_SITES[f['site']]['title']} ― {f['name']}"
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
    "today": "その日の予定を毎朝1本にまとめたもの。毎週の番組も含めて全部。",
}


def index_html(feeds, now, sources=()):
    out = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>hello-rss</title>", f"<style>{INDEX_CSS}</style></head><body><main>",
        "<h1>hello-rss</h1>",
        "<p>公式サイトの情報を RSS にしたものです（非公式）。リーダーにURLを登録して使います。</p>",
        '<p><a href="all.opml">全部まとめて登録する（OPML）</a></p>',
        '<p><a href="custom/">好きな人・グループ・カテゴリだけの RSS を作る</a></p>',
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
    for fsite, cfg in FC_SITES.items():
        fc = [f for f in feeds if f["site"] == fsite]
        if not fc:
            continue
        out.append(f"<h2>{escape(cfg['title'])}</h2><p>{escape(cfg['note'])}</p>")
        for part, label in (("category", "タブ"), ("group", "グループ別")):
            group = [f for f in fc if f.get("part") == part]
            if group:
                out.append(f'<p class="mute">{label}</p><ul>')
                for f in group:
                    out.append(f'<li><a href={quoteattr(f["url"])}>{escape(f["name"])}</a><code>{escape(f["url"])}</code></li>')
                out.append("</ul>")
    ameblo = next((s for s in sources if s["site"] == "ameblo"), None)
    if ameblo:
        out.append("<h2>アメブロ（公式のRSSがあります）</h2>"
                   "<p>ハロプロのグループ・メンバーのブログは、ブログごとに公式のRSSがあります。ブログ全体を見たいときは、これが一番確実です。"
                   "人で絞りたいとき（題名にその名前がある記事）や、言葉で絞りたいときは、"
                   '<a href="custom/">選んで作る RSS</a> の「アメブロ（ハロプロ）」が使えます。</p><ul>')
        for g in [w for w in ameblo["who"] if w["type"] == "group"]:
            for b in g["blogs"]:
                u = f"https://rssblog.ameba.jp/{b}/rss20.xml"
                out.append(f'<li><a href={quoteattr(u)}>{escape(g["label"])}（{escape(b)}）</a><code>{escape(u)}</code></li>')
        out.append("</ul>")
    yt = next((s for s in sources if s["site"] == "youtube"), None)
    if yt:
        out.append("<h2>YouTube（公式のRSSがあります）</h2>"
                   "<p>チャンネルごとに公式のRSSがあります（動画とShortsが混ざります）。動画とShortsを分けたいとき、複数のチャンネルをまとめたいとき、"
                   "動画を埋め込みたいときは、"
                   '<a href="custom/">選んで作る RSS</a> の「YouTube（ハロプロ公式）」が使えます。</p><ul>')
        for w in yt["who"]:
            u = f"https://www.youtube.com/feeds/videos.xml?channel_id={w['channels'][0]}"
            out.append(f'<li><a href={quoteattr(u)}>{escape(w["label"])}</a><code>{escape(u)}</code></li>')
        out.append("</ul>")
    out.append("<h2>ハロショ（公式のRSSがあります）</h2>"
               "<p>ハロー！プロジェクト オフィシャルショップは、公式がRSSを出しています（本文・画像つき）。"
               "タイトルだけにしたいときや、グループ・カテゴリ・人名で絞りたいときは、"
               '<a href="custom/">選んで作る RSS</a> の「ハロショ」が使えます。</p><ul>'
               '<li><a href="https://www.helloshop.info/feed">新着情報（全部）</a><code>https://www.helloshop.info/feed</code></li>'
               '<li><a href="https://www.helloshop.info/category/goods/feed">グッズ情報</a><code>https://www.helloshop.info/category/goods/feed</code></li>'
               '<li><a href="https://www.helloshop.info/category/photo/feed">生写真情報</a><code>https://www.helloshop.info/category/photo/feed</code></li>'
               '<li><a href="https://www.helloshop.info/category/news/feed">お知らせ</a><code>https://www.helloshop.info/category/news/feed</code></li></ul>')
    out.append(f'<p class="mute">更新: {now:%Y-%m-%d %H:%M} JST</p></main></body></html>\n')
    return "\n".join(out)


# Cloudflare（Workers の静的配信）に渡すヘッダー。フィードの種類を正しく伝える
def headers_file():
    rss = "  Content-Type: application/rss+xml; charset=utf-8"
    rules = [f"/{k}/*\n{rss}" for k in (*SITES, *FC_SITES)]
    rules += ["/all.opml\n  Content-Type: text/x-opml; charset=utf-8", "/data/*\n  Cache-Control: public, max-age=300"]
    return "\n".join(rules) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--feed-base", default="https://hello-rss.karin-archive.com")
    args = ap.parse_args()
    feed_base = args.feed_base.rstrip("/")
    now = dt.datetime.now(JST).replace(microsecond=0)
    rk = radiko.Radiko()

    feeds, failed, records, sources = [], [], [], []
    for site, cfg in SITES.items():
        try:
            f, rec, src = build_site(site, cfg, args.out, args.state_dir, feed_base, now, rk)
            feeds += f
            records.append(rec)
            sources += src
        except Exception as e:  # 片方のサイトが落ちても、もう片方は出す
            print(f"error: {site}: {e}", file=sys.stderr)
            failed.append(site)
    for fsite in FC_SITES:
        try:
            f, src = build_fc(args.out, args.state_dir, feed_base, now.replace(minute=0, second=0), fsite)
            feeds += f
            sources += src
        except Exception as e:
            print(f"error: {fsite}: {e}", file=sys.stderr)
            failed.append(fsite)
    for name, build in (("helloshop", lambda: build_shop(args.out, args.state_dir, now)),
                        ("youtube", lambda: [x for x in [youtube_source(args.state_dir)] if x])):
        try:
            sources += build()
        except Exception as e:
            print(f"error: {name}: {e}", file=sys.stderr)
            failed.append(name)
    if not feeds:
        sys.exit(1)

    write(os.path.join(args.out, "index.html"), index_html(feeds, now, sources))
    write(os.path.join(args.out, "all.opml"), opml(feeds))
    selectable.write_options(args.out, sources)
    write(os.path.join(args.out, "_headers"), headers_file())
    log_versions(os.path.join(args.state_dir, "observe.jsonl"), records, now)
    print(f"feeds: {len(feeds)}" + (f" (失敗: {', '.join(failed)})" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
