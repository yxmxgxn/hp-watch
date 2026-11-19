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
from email.utils import format_datetime
from html import unescape
from urllib.parse import urljoin
from xml.sax.saxutils import escape, quoteattr

import hpwatch
import radiko
import selectable

JST = dt.timezone(dt.timedelta(hours=9))
LIMIT = 50  # 1フィードに出す記事数
CAT_LABELS = {"NEWS": "お知らせ", "EVENT": "イベント", "TV": "テレビ", "RADIO": "ラジオ", "MAGAZINE": "雑誌", "WEB": "WEB"}
SITE_LABELS = {"hp": "ハロプロ", "jproom": "JPROOM", "fc": "ハロプロFC"}

SITES = {
    "hp": {"label": "ハロプロ", "base": "https://helloproject.com", "by_group": True},
    "jproom": {"label": "JPROOM", "base": "https://www.jp-r.co.jp", "by_group": False},
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
        "who": selectable.who_of(x.get("artistsSearch"), pm),  # 選択式フィード用
        "cats": [cat],
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
    feeds += emit_feeds(site, cfg, "media", media, profiles, out, feed_base, f"{base}/schedule/")

    path = f"{site}/today.xml"
    url = f"{feed_base}/{path}"
    name = "全体" if cfg["by_group"] else "全員"
    write(os.path.join(out, path), rss_xml(
        f"{cfg['label']} 今日の予定", f"{base} のスケジュールから、その日の予定を毎朝1本にまとめたもの・非公式",
        f"{base}/schedule/", url, newest(today_items(site, base, schedules, now, rk))))
    feeds.append({"site": site, "kind": "today", "name": name, "url": url})

    # 選択式フィードの元データ（人・グループは、プロフィールの順。種別: group・unit はグループ、member・solo は人）
    who_info = list(pm.values())
    sources = [
        selectable.make_source(out, site, SITE_LABELS[site], "news", "お知らせ", news, who_info,
                               [(k, CAT_LABELS[k]) for k in ("NEWS", "EVENT")]),
        selectable.make_source(out, site, SITE_LABELS[site], "schedule", "スケジュール（新しく載った出演）", media, who_info,
                               [(k, CAT_LABELS[k]) for k in MEDIA_CATS], who_all=True),
    ]

    # フィードを書けてから状態を保存する
    save_state(state_path, state)
    save_state(os.path.join(state_dir, f"rss_news_{site}.json"), news_state)
    return feeds, {"site": site, "version": version, "news": news_found, "media": media_found}, sources


# ---------- ファンクラブニュース ----------

FC_BASE = "https://www.upfc.jp"
FC_LIST = FC_BASE + "/helloproject/news_list.php"
FC_KEEP = 300   # 状態ファイルに残す記事数
FC_PAGES = 3    # 「すべて」を1回に見るページ数の上限（1ページ15件）
# 公式のタブの順。ニュースはタブには無いが、「すべて」の中にある分類
FC_FEEDS = [("all", "全体"), ("news", "ニュース"), ("event", "イベント"), ("concert", "コンサート"),
            ("goods", "グッズ"), ("stream", "配信"), ("other", "その他")]
FC_CATS = {"ニュース": "news", "イベント": "event", "コンサート": "concert", "グッズ": "goods", "配信": "stream", "その他": "other"}
# グループ別（フィードの名前 → ファンクラブのグループのページ）。ハロプロのお知らせのフィードと名前をそろえる
FC_GROUPS = [("morningmusume", "MORNING", "モーニング娘。'26"), ("angerme", "ANGERME", "アンジュルム"),
             ("juicejuice", "JUICEJUICE", "Juice=Juice"), ("tsubakifactory", "TSUBAKIFACTORY", "つばきファクトリー"),
             ("beyooooonds", "BEYOOOOONDS", "BEYOOOOONDS"), ("ochanorma", "OCHANORMA", "OCHA NORMA"),
             ("rosychronicle", "ROSYCHRONICLE", "ロージークロニクル"), ("helloprokenshusei", "KENSYUSEI", "ハロプロ研修生")]
FC_ITEM_RE = re.compile(
    r'<li>\s*<a href="([^"]+)">\s*<p class="news__date[^"]*">\s*([\d.]+)\s*<span class="news__ctg">([^<]*)</span>\s*</p>'
    r'\s*<p class="news__txt[^"]*">(.*?)</p>', re.S)


def fc_get(url):
    time.sleep(0.3)  # 続けて取りに行かない
    return hpwatch.get(url)


def fc_parse(page, url):
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
        rows.append({"guid": f"fc-{key}", "d": d, "jd": dt.date(y, mo, da), "cat": cat.strip(), "title": title, "link": link})
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
        "fcgroups": sorted(groups),
    }


def update_fc(state, stamp):
    """一覧を見て、新しく載った記事を足す。戻り値: (記事, 新しい状態, 新しく見つけた件数)"""
    first = state is None
    items = list((state or {}).get("items") or [])
    gids = dict((state or {}).get("gids") or {})
    by_guid = {i["guid"]: i for i in items}

    # 「すべて」。初回は2ページ分で始める。ふだんは、見たことのある記事に当たったら、それより先は新しくない
    fetched = []
    for pg in range(1, FC_PAGES + 1):
        url = FC_LIST + ("?@rst=all" if pg == 1 else f"?@pg={pg}&@tbl=news")
        rows = fc_parse(fc_get(url), url)
        fetched += [(r, len(fetched) + n) for n, r in enumerate(rows)]
        if (first and pg >= 2) or (not first and any(r["guid"] in by_guid for r in rows)):
            break
    in_all = {r["guid"] for r, _ in fetched}

    # グループ別（1ページ目だけ）。記事そのものにはグループの印が無いので、どのグループの一覧に載っているかで決める。
    # 2ページ目以降は、絞り込みがセッションに依るので見ない。1グループが失敗しても、ほかは続ける
    member = {}
    for slug, uid, _ in FC_GROUPS:
        try:
            if slug not in gids:  # グループの番号（gid）は、グループのページの「view more」のリンクにある
                m = re.search(r"news_list\.php\?gid=(\d+)", fc_get(f"{FC_BASE}/helloproject/artist/?@uid={uid}&@rst=all"))
                if not m:
                    raise RuntimeError("グループの番号が見つからない")
                gids[slug] = int(m.group(1))
            url = f"{FC_LIST}?gid={gids[slug]}&@rst=all"
            rows = fc_parse(fc_get(url), url)
        except Exception as e:
            print(f"warn: fc {slug}: {e}", file=sys.stderr)
            continue
        for pos, r in enumerate(rows):
            member.setdefault(r["guid"], set()).add(slug)
            if r["guid"] not in in_all:  # 「すべて」の範囲より古いが、グループの一覧にはある記事
                in_all.add(r["guid"])
                fetched.append((r, 50 + pos))

    for i in items:  # 既に持っている記事に、グループを足す
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
    return items, {"items": items, "gids": gids}, 0 if first else len(new)


def build_fc(out, state_dir, feed_base, stamp):
    """ファンクラブニュースのフィードを書き出す。取得に失敗しても、前回の状態があればそれで書く（他のフィードの公開を止めない）"""
    path = os.path.join(state_dir, "rss_fc.json")
    state = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    try:
        items, new_state, found = update_fc(state, stamp)
    except Exception as e:
        if state is None:
            raise
        print(f"warn: fc: {e}（前回の状態で書く）", file=sys.stderr)
        items, new_state, found = state["items"], None, 0

    feeds = []

    def emit(rel, name, part, subset):
        url = f"{feed_base}/{rel}"
        write(os.path.join(out, rel), rss_xml(
            f"ファンクラブ ニュース ― {name}", f"{FC_BASE}/helloproject/ のファンクラブニュース（{name}）・非公式",
            FC_LIST + "?@rst=all", url, newest(subset)))
        feeds.append({"site": "fc", "kind": "news", "name": name, "url": url, "part": part})

    for slug, name in FC_FEEDS:
        emit(f"fc/{slug}.xml", name, "category", items if slug == "all" else [i for i in items if FC_CATS.get(i["cat"]) == slug])
    for slug, _, name in FC_GROUPS:
        emit(f"fc/group/{slug}.xml", name, "group", [i for i in items if slug in (i.get("fcgroups") or [])])
    if new_state is not None:
        save_state(path, new_state)
    print(f"fc news: 記事{len(items)} 新規{found}{'（初回）' if state is None else ''}")
    rows = [dict(i, who=i.get("fcgroups") or [], cats=[FC_CATS[i["cat"]]] if i["cat"] in FC_CATS else []) for i in items]
    source = selectable.make_source(out, "fc", SITE_LABELS["fc"], "news", "ニュース", rows,
                                    [(slug, name, "group") for slug, _, name in FC_GROUPS],
                                    [(k, n) for k, n in FC_FEEDS if k != "all"], who_all=True)
    return feeds, [source]


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
    if f["site"] == "fc":
        return f"ファンクラブ ニュース ― {f['name']}"
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
    "fc": "Hello! Project オフィシャルファンクラブのニュース。公式のタブどおりに分けています（ニュースだけは、タブには無い分類です）。",
}


def index_html(feeds, now):
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
    fc = [f for f in feeds if f["site"] == "fc"]
    if fc:
        out.append(f"<h2>ファンクラブ ニュース</h2><p>{escape(KIND_NOTE['fc'])}</p>")
        for part, label in (("category", "タブ"), ("group", "グループ別")):
            out.append(f'<p class="mute">{label}</p><ul>')
            for f in [f for f in fc if f.get("part") == part]:
                out.append(f'<li><a href={quoteattr(f["url"])}>{escape(f["name"])}</a><code>{escape(f["url"])}</code></li>')
            out.append("</ul>")
    out.append(f'<p class="mute">更新: {now:%Y-%m-%d %H:%M} JST</p></main></body></html>\n')
    return "\n".join(out)


# Cloudflare（Workers の静的配信）に渡すヘッダー。フィードの種類を正しく伝える
HEADERS = """/hp/*
  Content-Type: application/rss+xml; charset=utf-8
/jproom/*
  Content-Type: application/rss+xml; charset=utf-8
/fc/*
  Content-Type: application/rss+xml; charset=utf-8
/all.opml
  Content-Type: text/x-opml; charset=utf-8
/data/*
  Cache-Control: public, max-age=300
"""


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
    try:
        f, src = build_fc(args.out, args.state_dir, feed_base, now.replace(minute=0, second=0))
        feeds += f
        sources += src
    except Exception as e:
        print(f"error: fc: {e}", file=sys.stderr)
        failed.append("fc")
    if not feeds:
        sys.exit(1)

    write(os.path.join(args.out, "index.html"), index_html(feeds, now))
    write(os.path.join(args.out, "all.opml"), opml(feeds))
    selectable.write_options(args.out, sources)
    write(os.path.join(args.out, "_headers"), HEADERS)
    log_versions(os.path.join(args.state_dir, "observe.jsonl"), records, now)
    print(f"feeds: {len(feeds)}" + (f" (失敗: {', '.join(failed)})" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
