"""ポッドキャスト・YouTube の、公開されたエピソードを、毎週の番組の1回分に結びつけて、manual/auto_episodes.json に書く

  python episodes.py [--sources manual/sources.json] [--out manual/auto_episodes.json] [--episodes manual/episodes.json] [--list]

番組ごとの行き先は manual/sources.json（手で書く）。そこに podcast（公開のRSS）か youtube_channel（チャンネルのID）がある番組が対象。
- ポッドキャスト: 番組の RSS の最新のエピソードを、毎週の番組の1回分（放送日が今日から14日前までのもの）に結びつける
    エピソードの題名に日付があれば（「2026年11月15日放送分」「11/16」など）、放送日と合うものを選ぶ。日付が無ければ、公開日が放送日の6日前から10日後までの、放送日にいちばん近いもの（同じ週の再放送・遅れ放送にも、同じエピソード）
    題名に別の日付があるエピソードは、放送日が違うので選ばない。リンクはエピソードのページ（無ければ音声のURL）。リンクの名前は「ポッドキャスト」
- YouTube: チャンネルの公式のRSS（最新15本）から、題名に番組名がある動画のうち、題名の日付（「11月22日放送回」）が放送日と合うもの。題名に日付が無ければ、放送日（日本時間）に公開されたもの。リンクの名前は「YouTube」
- Spotify は、ここでは扱わない（公開のRSSが無い番組があるため。番組のページのリンクだけ、sources.json から rss.py が付ける）
- 番組ごとの最新のエピソード（5件）は、manual/episodes.json に残す（手で入れるページの「毎週」が、反映を確かめるのに使う）
"""
import argparse
import datetime as dt
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import rss

JST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (hp-watch; episodes)"
BACK = 14           # 放送日が、今日から何日前までの1回分を探すか
UNTIL_DAYS = 14     # 結びつけたリンクを、放送日から何日まで出すか
LATE_DAYS = 10      # ポッドキャストの公開が、放送日から何日遅れまで許すか
BEFORE_DAYS = 6     # 公開が、放送日の何日前まで（同じ週の、別の曜日・別の局の放送）許すか
KEEP = 5            # 番組ごとに残す最新のエピソード
FULL_DATE = re.compile(r"(\d{4})\s*[年./]\s*(\d{1,2})\s*[月./]\s*(\d{1,2})")
MD_DATE = re.compile(r"(?<![\d/.])(\d{1,2})\s*[/月]\s*(\d{1,2})(?![\d/])")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def jst(d):
    return d.astimezone(JST).date()


# ---------- 読む ----------

def parse_rss(text):
    """ポッドキャストの RSS → [{title, link, audio, date(JSTの日付)}]（新しい順）"""
    root = ET.fromstring(text)
    ch = root.find("channel")
    out = []
    for it in (ch.findall("item") if ch is not None else []):
        pub = (it.findtext("pubDate") or "").strip()
        try:
            d = jst(parsedate_to_datetime(pub))
        except (TypeError, ValueError):
            continue
        enc = it.find("enclosure")
        out.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                    "audio": (enc.get("url") if enc is not None else "") or "", "date": d})
    return sorted(out, key=lambda e: e["date"], reverse=True)


def parse_youtube(text):
    """YouTube の RSS（Atom）→ [{title, link, published(JSTの日付)}]"""
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}
    root = ET.fromstring(text)
    out = []
    for e in root.findall("a:entry", ns):
        pub = (e.findtext("a:published", namespaces=ns) or "").strip()
        link = e.find("a:link", ns)
        try:
            d = jst(dt.datetime.fromisoformat(pub))
        except ValueError:
            continue
        desc = (e.findtext("m:group/m:description", namespaces=ns) or "").strip()
        out.append({"title": (e.findtext("a:title", namespaces=ns) or "").strip(), "link": (link.get("href") if link is not None else ""), "date": d,
                    "desc": desc.split("\n", 1)[0].strip()[:80]})   # 説明欄の1行目（「2026年11月17日放送 #453」「YouTube版 #94」のように、回が書いてあることがある）
    return out


# ---------- 結びつける ----------

def title_dates(title, ref):
    """題名に書かれた日付の集合。年があればその年、無ければ、公開日（ref）にいちばん近い年"""
    found = set()
    for y, m, d in FULL_DATE.findall(title):
        try:
            found.add(dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    rest = FULL_DATE.sub(" ", title)
    for m, d in MD_DATE.findall(rest):
        best = None
        for y in (ref.year - 1, ref.year, ref.year + 1):
            try:
                c = dt.date(y, int(m), int(d))
            except ValueError:
                continue
            if best is None or abs((c - ref).days) < abs((best - ref).days):
                best = c
        if best:
            found.add(best)
    return found


def match_podcast(episodes, air):
    """放送日（air）の1回分に合うエピソード。無ければ None。
    題名に日付があれば、放送日と合うもの。日付が無ければ、公開日が放送日の6日前から10日後までの、放送日にいちばん近いもの（同点なら後のもの）。
    同じ週に、別の曜日・別の局で放送される分（再放送・遅れ放送）にも、同じエピソードが結びつく。題名に別の日付があるエピソードは、選ばない"""
    free = [e for e in episodes if e["title"] and (e["link"] or e["audio"])]
    for e in free:  # 題名の日付が、放送日と合う
        if air in title_dates(e["title"], e["date"]):
            return e
    near = [e for e in free if not title_dates(e["title"], e["date"])
            and air - dt.timedelta(days=BEFORE_DAYS) <= e["date"] <= air + dt.timedelta(days=LATE_DAYS)]
    return min(near, key=lambda e: (abs((e["date"] - air).days), -e["date"].toordinal())) if near else None


def video_dates(v):
    """動画の題名・説明欄の1行目に書かれた日付（「2026年11月17日放送」など）"""
    return title_dates(v["title"] + " " + (v.get("desc") or ""), v["date"])


def match_youtube(videos, name_words, air, dedicated=False):
    """題名に番組名がある動画のうち、放送日（air）に合うもの。無ければ None。
    題名・説明欄の1行目に日付があれば（「11月22日放送回」「2026年11月17日放送」。放送の数日後・翌朝に上がる）、その日付が放送日と合うもの。
    日付が無ければ、放送日（日本時間）に公開されたもの。別の日付があるものは、公開日が放送日でも選ばない。複数あれば、最初のもの。
    dedicated（番組専用のチャンネル）なら、題名に番組名が無くてもよい（BOOKSTAND.TV のように、題名がその回のテーマの文のとき）"""
    words = [rss.norm_name(w) for w in name_words if rss.norm_name(w)]
    mine = sorted((v for v in videos if v["link"] and (dedicated or any(w in rss.norm_name(v["title"]) for w in words))), key=lambda v: v["link"])
    for v in mine:
        if air in video_dates(v):
            return v
    for v in mine:
        if v["date"] == air and not video_dates(v):
            return v
    return None


def youtube_label(v):
    """リンクの名前。説明欄が「YouTube版」なら『YouTube版』（放送ではなく、YouTube 用の回）。放送の日付が書いてあれば『YouTube（放送分）』。ほかは『YouTube』"""
    desc = v.get("desc") or ""
    if "YouTube版" in desc or "YouTube版" in v.get("title", ""):
        return "YouTube版"
    if "放送" in desc and title_dates(desc, v["date"]):
        return "YouTube（放送分）"
    return "YouTube"


PREV_LONG = 10   # 直前の回が、これより前（日）なら、『先週分』ではなく『前の回』


def prev_podcast(eps, air):
    """放送日（air）の回がまだ無いとき、直前の回（その放送より前の、いちばん新しい回）。(エピソード, 回の日付) または None。
    回の日付は、題名に日付があればそれ、無ければ公開日。放送の14日前まで"""
    best = None
    for e in eps:
        if not e["title"] or not (e["link"] or e["audio"]):
            continue
        ref = max(title_dates(e["title"], e["date"]) or {e["date"]})
        if ref < air and (air - ref).days <= 14 and (best is None or ref > best[1]):
            best = (e, ref)
    return best


def prev_label(base, gap_days):
    return f"{base}（先週分）" if gap_days <= PREV_LONG else f"{base}（前の回）"


def ep_info(e, prev=False):
    """どの回か（手で入れるページが、放送日とのずれを見せるのに使う）: 公開日・題名・題名に書かれた日付（あれば）"""
    titled = sorted(title_dates(e["title"] + " " + (e.get("desc") or ""), e["date"]))
    d = {"date": e["date"].isoformat(), "kind": "pub", "title": (e["title"] or "")[:60]}
    if e.get("desc"):
        d["note"] = e["desc"][:60]
    if titled:
        d["titled"] = titled[0].isoformat()
    if prev:
        d["prev"] = True   # 今週の回がまだ無くて、直前の回を付けた分（本当の回が出たら差し替える）
    return d


def episode_link(e):
    return e["link"] if e["link"].startswith("https://") else (e["audio"] if e["audio"].startswith("https://") else "")


# ---------- 実行 ----------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f) or default
    return default


def occurrences(today):
    """毎週・毎月の番組の、放送日が今日から BACK 日前までの1回分（全部の種類）。公式のデータを、rss.py と同じ作りで読む"""
    import hpwatch
    now = dt.datetime.now(JST)
    out = {}
    for site, cfg in rss.SITES.items():
        try:
            version, years = hpwatch.fetch_version(cfg["base"])
            schedules, artists = hpwatch.fetch_data(cfg["base"], version, years)
        except Exception as e:  # 1つのサイトの失敗で、全体を止めない
            print(f"  {site}: 公式のデータが取れない {str(e)[:60]}")
            continue
        for o in rss.weekly_occurrences(site, cfg["label"], schedules, artists.get("profilesById") or {}, now, None, {}, []):
            air = dt.date.fromisoformat(o["air"])
            if today - dt.timedelta(days=BACK) <= air <= today:
                out[o["guid"]] = o
    return list(out.values())


def merge_entries(old, found, today):
    """今の分に、見つけた分を足す（同じ項目は、リンクをまとめる）。期限の切れた分は外す"""
    keep = {e["guid"]: e for e in old if str(e.get("until") or "") >= today.isoformat()}
    for e in found:
        cur = keep.get(e["guid"])
        if cur:
            # 本当の回が出たら（または直前の回が変わったら）、昔の『先週分』『前の回』は外す（同じ種類のリンクで、URL が違うもの）
            for l in e["links"]:
                base = l["label"].split("（")[0]
                cur["links"] = [x for x in cur.get("links") or []
                                if not ((x.get("ep") or {}).get("prev") and x["label"].split("（")[0] == base and x["url"] != l["url"])]
            have_links = {l["url"]: l for l in cur.get("links") or []}
            for l in e["links"]:
                if l["url"] in have_links:
                    if l.get("ep") and have_links[l["url"]].get("ep") != l["ep"]:
                        have_links[l["url"]]["ep"] = l["ep"]   # 昔の分にも、どの回かを足す
                else:
                    cur["links"] = (cur.get("links") or []) + [l]
        else:
            keep[e["guid"]] = e
    return sorted(keep.values(), key=lambda e: (str(e.get("air")), e["guid"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="manual/sources.json")
    ap.add_argument("--out", default="manual/auto_episodes.json")
    ap.add_argument("--episodes", default="manual/episodes.json")
    ap.add_argument("--list", action="store_true", help="探す1回分を出すだけ（RSS は読まない）")
    args = ap.parse_args()

    today = dt.datetime.now(JST).date()
    sources = [p for p in load_json(args.sources, {}).get("programs", []) if p.get("podcast") or p.get("youtube_channel")]
    old = load_json(args.out, {}).get("items") or []
    have = {(e["guid"], l["url"]) for e in old for l in e.get("links") or []}
    occs = occurrences(today)
    print(f"対象の番組 {len(sources)} 本・毎週の1回分 {len(occs)} 件（今日 {today}）")

    by_source = {}
    for o in occs:
        src = rss.source_for(o["prog"], sources)
        if src:
            by_source.setdefault(src["name"], (src, []))[1].append(o)
    if args.list:
        for name, (src, os_) in by_source.items():
            print("  ", name, sorted({o["air"] for o in os_}))
        return

    found, latest = [], {}
    for src in sources:
        name = src["name"]
        occ = by_source.get(name, (src, []))[1]
        try:
            if src.get("podcast"):
                eps = parse_rss(fetch(src["podcast"]))
                latest[name] = [{"title": e["title"], "link": episode_link(e), "date": e["date"].isoformat()} for e in eps[:KEEP]]
                for air_s in sorted({o["air"] for o in occ}):
                    air = dt.date.fromisoformat(air_s)
                    e = match_podcast(eps, air)
                    if not e:
                        pv = prev_podcast(eps, air) if air >= today - dt.timedelta(days=7) else None   # 今週（直近7日）の放送には、直前の回を『先週分』として付ける
                        if pv:
                            pe, ref = pv
                            for o in (o for o in occ if o["air"] == air_s):
                                found.append({"guid": o["guid"], "air": air_s, "until": str(air + dt.timedelta(days=UNTIL_DAYS)),
                                              "links": [{"label": prev_label("ポッドキャスト", (air - ref).days), "url": episode_link(pe), "ep": ep_info(pe, prev=True)}],
                                              "source": "podcast", "found": today.isoformat()})
                            print(f"  {name} {air_s}: 今週の回がまだ無い。直前の回（{ref}）を付ける {episode_link(pe)}")
                        else:
                            print(f"  {name} {air_s}: エピソードが無い（{len(eps)} 件）")
                        continue
                    for o in (o for o in occ if o["air"] == air_s):
                        found.append({"guid": o["guid"], "air": air_s, "until": str(air + dt.timedelta(days=UNTIL_DAYS)),
                                      "links": [{"label": "ポッドキャスト", "url": episode_link(e), "ep": ep_info(e)}], "source": "podcast", "found": today.isoformat()})
                    print(f"  {name} {air_s}: {episode_link(e)}")
            if src.get("youtube_channel"):
                vids = parse_youtube(fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={src['youtube_channel']}"))
                for air_s in sorted({o["air"] for o in occ}):
                    air = dt.date.fromisoformat(air_s)
                    v = match_youtube(vids, src.get("match") or [name], air, dedicated=bool(src.get("youtube_dedicated")))
                    if not v:
                        print(f"  {name} {air_s}: 動画が無い（{len(vids)} 本）")
                        continue
                    for o in (o for o in occ if o["air"] == air_s):
                        found.append({"guid": o["guid"], "air": air_s, "until": str(air + dt.timedelta(days=UNTIL_DAYS)),
                                      "links": [{"label": youtube_label(v), "url": v["link"], "ep": ep_info(v)}], "source": "youtube", "found": today.isoformat()})
                    print(f"  {name} {air_s}: {v['link']}")
        except Exception as e:  # 1つの番組の失敗で、全体を止めない
            print(f"  {name}: 失敗 {str(e)[:80]}")

    fresh = [e for e in found if not all((e["guid"], l["url"]) in have for l in e["links"])]
    items = merge_entries(old, found, today)   # 同じ項目・同じURLは、リンクをまとめて、どの回か（ep）だけ足す
    write_if_changed(args.out, {"items": items}, f"自動で入れた分（新しく {len(fresh)} 件）")
    if latest:
        write_if_changed(args.episodes, {"updated": today.isoformat(), "programs": latest}, "番組ごとの最新のエピソード", ignore="updated")


def write_if_changed(path, data, what, ignore=None):
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if ignore and old:
        try:
            cmp = {k: v for k, v in json.loads(old).items() if k != ignore}
            if cmp == {k: v for k, v in data.items() if k != ignore}:
                print(f"変更なし: {path}")
                return
        except ValueError:
            pass
    if text != old:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"書いた: {path}（{what}）")
    else:
        print(f"変更なし: {path}")


if __name__ == "__main__":
    main()
