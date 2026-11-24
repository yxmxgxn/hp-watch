"""ハロー！プロジェクト公式サイトのスケジュール差分ウォッチャー（試作）

流れ:
  1. /schedule/ のHTMLから versionDir と対象年を取る（前回と同じなら終了）
  2. /json/{versionDir}/{year}_schedules.json と artist_list.json を取る
  3. 単発スケジュール / レギュラー番組 / イベント に分けて前回状態と比較
  4. 新しく増えたものだけを通知（DISCORD_WEBHOOK_URL があればDiscord、なければ標準出力）
     変更・削除・レギュラー終了は通知しない

使い方:
  python hpwatch.py [--state state.json] [--name ハロプロ] [--force] [--base https://helloproject.com]
初回は状態を作るだけで通知しない。
状態ファイルは初回と差分があったときだけ書き換える（判定方法を変えたら状態ファイルを消して作り直す）。
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (hp-watch; personal schedule diff)"
BASE = "https://helloproject.com"

# 通知しないタイトル（正規表現）。mute.txt に1行1パターンで書く（# で始まる行はコメント）
MUTE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mute.txt")
MUTE_TITLES = []
if os.path.exists(MUTE_FILE):
    with open(MUTE_FILE, encoding="utf-8-sig") as f:  # メモ帳で保存してもBOMを無視
        MUTE_TITLES = [s.strip() for s in f if s.strip() and not s.lstrip().startswith("#")]

# schedule.label がこれに当たるものは定期番組扱い（日付ごとの追加を通知しない）
RECURRING = r"毎週|隔週|毎月|毎日|月[〜～-]金"


# ---------- 取得 ----------

# 取った公式のJSONの ETag（中身のハッシュ。作り直されても、中身が同じなら変わらない）。{サイト: {ファイル名: ETag}}
# rss.py が、公開のデータ（data/watch.json）に書く。Worker は、公式の今の ETag と比べて、変わったときだけ rss.yml を呼ぶ
ETAGS = {}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
        etag = r.headers.get("ETag")
        if etag and "/json/" in url:
            site, _, rest = url.partition("/json/")
            ETAGS.setdefault(site, {})[rest.rsplit("/", 1)[-1]] = etag
        return body


def fetch_version(base):
    # 版（versionDir）は、どのページにも入っている。/schedule/ が無いサイト（個人の公式サイトなど）は /news/ から読む
    try:
        page = html.unescape(get(f"{base}/schedule/"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        page = html.unescape(get(f"{base}/news/"))
    m = re.search(r'"versionDir":\[0,"([^"]+)"\]', page)
    if not m:
        raise RuntimeError("versionDir が見つからない（サイト構造が変わった？）")
    ym = re.search(r'"years":\[1,(\[.*?\])\]', page)
    years = sorted({y for y in re.findall(r'"(\d{4})"', ym.group(1))}) if ym else []
    return m.group(1), years


def fetch_data(base, version, years):
    schedules = []
    for y in years:
        try:
            schedules += json.loads(get(f"{base}/json/{version}/{y}_schedules.json"))["items"]
        except Exception as e:  # 先の年はまだ無いことがある
            print(f"warn: {y}_schedules.json: {e}", file=sys.stderr)
    artists = json.loads(get(f"{base}/json/{version}/artist_list.json"))
    return schedules, artists


# ---------- 正規化 ----------

def link_of(x):
    return (x.get("link") or {}).get("link") or ""


def text_of(x):
    if isinstance(x, dict):
        return x.get("content") or ""
    return x or ""


def regular_key(x):
    s = x.get("schedule") or {}
    return "|".join([text_of(x.get("title")), link_of(x), s.get("label") or "", s.get("start") or ""])


def ical_id(x):
    m = re.search(r"/(schedule|regular)_(\d+)_", (x.get("calendar") or {}).get("ical") or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def compact(x):
    """比較・通知に使うフィールドだけ残す（calendar, isUpdate は捨てる）"""
    s = x.get("schedule") or {}
    return {
        "date": x.get("date"),
        "category": x.get("category"),
        "title": text_of(x.get("title")).strip(),
        "link": link_of(x),
        "note": text_of(x.get("note")),
        "status": x.get("status"),
        "isFc": bool(x.get("isFc")),
        "start": s.get("start"),
        "end": s.get("end"),
        "label": s.get("label"),
        "artists": sorted(x.get("artistsSearch") or []),
        "members": sorted(x.get("membersSearch") or []),
    }


def build_snapshot(schedules, artists):
    profiles = artists.get("profilesById") or {}
    names = {str(k): v.get("nameJa") or v.get("slug") for k, v in profiles.items()}

    # レギュラー番組（サイトが regular と認めているもの）
    regulars = {}
    for p in profiles.values():
        for r in p.get("regular") or []:
            regulars.setdefault(regular_key(r), compact(r))

    # イベント（同じものが複数プロフィールに載るので link で重複排除）
    events = {}
    for p in profiles.values():
        for e in p.get("events") or []:
            k = link_of(e) or e.get("title")
            s = e.get("schedule") or {}
            events[k] = {
                "title": e.get("title"),
                "link": link_of(e),
                "start": s.get("start"),
                "end": s.get("end"),
                "artists": sorted(e.get("artistsSearch") or []),
            }

    # 単発スケジュール
    singles = {}
    for x in schedules:
        kind, iid = ical_id(x)
        if kind == "regular" or regular_key(x) in regulars:
            continue
        c = compact(x)
        # regular に登録漏れの定期番組（事務所はそんなにまめじゃない）
        if re.search(RECURRING, c["label"] or ""):
            r = dict(c, date=None)
            regulars.setdefault(regular_key(x), r)
            continue
        if kind == "schedule":
            key = f"s{iid}"
        else:
            raw = json.dumps([c["date"], c["title"], c["link"], c["start"], c["end"], c["members"]], ensure_ascii=False)
            key = "h" + hashlib.sha1(raw.encode()).hexdigest()[:16]
        base_key, n = key, 1
        while key in singles:  # 完全重複（別局の同時刻放送など）
            n += 1
            key = f"{base_key}#{n}"
        singles[key] = c

    return {"singles": singles, "regulars": regulars, "events": events, "names": names}


# ---------- 差分 ----------

WATCH_FIELDS = ["date", "title", "link", "note", "status", "isFc", "start", "end", "members", "category"]


def diff_section(old, new, pair_by="link"):
    added = {k: v for k, v in new.items() if k not in old}
    removed = {k: v for k, v in old.items() if k not in new}
    changed = []
    for k in new.keys() & old.keys():
        fields = [f for f in WATCH_FIELDS if f in new[k] and old[k].get(f) != new[k].get(f)]
        if fields:
            changed.append((old[k], new[k], fields))

    # 中身が同じ削除と追加は、キーが付け替わっただけ（日付が過ぎると公式サイトが ical を外すので、
    # s{id} → h{ハッシュ} に変わる）。通知しない。同じリンクの別番組が複数あっても対応できるよう、
    # リンクではなく中身そのもので突き合わせる
    def body(v):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    same = {}
    for k, v in added.items():
        same.setdefault(body(v), []).append(k)
    for k in list(removed):
        ks = same.get(body(removed[k]))
        if ks:
            del added[ks.pop()], removed[k]

    # キーがハッシュの項目は、日付やタイトルが変わると「削除＋追加」に見える。
    # 同じリンク（＋カテゴリ）の削除と追加が1対1なら「変更」とみなす
    def pkey(v):
        return (v.get(pair_by), v.get("category"))
    rm_by = {}
    for k, v in removed.items():
        if v.get(pair_by):
            rm_by.setdefault(pkey(v), []).append(k)
    ad_by = {}
    for k, v in added.items():
        if v.get(pair_by):
            ad_by.setdefault(pkey(v), []).append(k)
    for pk, aks in ad_by.items():
        rks = rm_by.get(pk) or []
        if len(aks) == 1 and len(rks) == 1:
            o, n = removed.pop(rks[0]), added.pop(aks[0])
            fields = [f for f in WATCH_FIELDS if f in n and o.get(f) != n.get(f)]
            if fields:  # 空なら見た目は同じ。黙る
                changed.append((o, n, fields))
    return added, removed, changed


def is_muted(v):
    return any(re.search(p, v.get("title") or "") for p in MUTE_TITLES)


# ---------- 通知 ----------

def who(v, names):
    ids = v.get("members") or []
    if len(ids) >= 30:
        return "全員"
    ns = [names.get(str(i), str(i)) for i in ids] or [names.get(str(i), str(i)) for i in v.get("artists") or []]
    return "、".join(ns)


def fmt_item(v, names):
    if v.get("date"):
        when, t = v["date"], v.get("start") or ""
    elif v.get("label"):  # レギュラー
        when, t = v["label"], v.get("start") or ""
    else:  # イベント
        s, e = v.get("start") or "", v.get("end") or ""
        when, t = (s if s == e else f"{s}〜{e}"), ""
    tags = []
    if v.get("isFc"):
        tags.append("FC")
    if v.get("status"):
        tags.append(v["status"])
    head = f'{when} {t}'.strip()
    cat = f'[{v["category"]}]' if v.get("category") else ""
    lines = [f'{head} {cat}{"".join(f"[{x}]" for x in tags)} {v.get("title")}'.strip()]
    w = who(v, names)
    if w:
        lines.append(f"  出演: {w}")
    if v.get("note"):
        lines.append("  " + v["note"].replace("\n", " "))
    if v.get("link"):
        link = v["link"]
        lines.append("  " + (BASE + link if link.startswith("/") else link))
    return "\n".join(lines)


def build_messages(prev, cur):
    """新規・レギュラー開始・イベント発表だけを通知する（変更・削除・終了は通知しない）。
    diff_section の変更・削除は捨てるが、突き合わせ（キーの付け替えや変更を「新規」と誤認しない）には使っている"""
    names = cur["names"]
    msgs = []
    a, _, _ = diff_section(prev["singles"], cur["singles"])
    msgs += [("新規", fmt_item(v, names)) for v in sorted(a.values(), key=lambda v: v["date"] or "") if not is_muted(v)]

    a, _, _ = diff_section(prev["regulars"], cur["regulars"])
    msgs += [("レギュラー開始", fmt_item(v, names)) for v in a.values() if not is_muted(v)]

    a, _, _ = diff_section(prev["events"], cur["events"])
    msgs += [("イベント発表", fmt_item(v, names)) for v in a.values()]
    return msgs


# 埋め込みの左端の色。新規・開始・発表＝緑（中止が付いていたら赤）
# YELLOW・RED は profiles.py（プロフィールの変更・消滅）も使う。消さないこと
GREEN, YELLOW, RED = 0x2ECC71, 0xF1C40F, 0xE74C3C


def to_embed(kind, body, site):
    head, *rest = body.split("\n")
    color = RED if "[中止]" in head else GREEN
    embed = {
        "title": f"【{kind}】{head}"[:256],
        "description": "\n".join(s.strip() for s in rest)[:4000],
        "color": color,
        "footer": {"text": site},
    }
    link = next((s.strip() for s in rest if s.strip().startswith("http")), None)
    if link:
        embed["url"] = link
    return embed


def notify(msgs, site):
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook:
        print(f"=== {site}: {len(msgs)}件 ===")
        print("\n\n".join(f"【{site}】【{kind}】{body}" for kind, body in msgs))
        return
    send_embeds(hook, [to_embed(kind, body, site) for kind, body in msgs], site)


def send_embeds(hook, embeds, site, suffix="予定"):
    # Discord は1メッセージに埋め込み10個・合計6000文字まで
    batch, size = [], 0
    for e in embeds:
        n = len(e["title"]) + len(e["description"]) + len(site)
        if batch and (len(batch) == 10 or size + n > 5500):
            post(hook, batch, site, suffix)
            batch, size = [], 0
        batch.append(e)
        size += n
    if batch:
        post(hook, batch, site, suffix)


def post(hook, embeds, site, suffix="予定"):
    data = json.dumps({"username": f"{site}{suffix}", "embeds": embeds}).encode()
    req = urllib.request.Request(hook, data=data, headers={"Content-Type": "application/json", "User-Agent": UA})
    for attempt in range(5):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:  # 429 は連投の制限。待って再送
                raise
            time.sleep(float(e.headers.get("Retry-After") or 2))
    time.sleep(0.5)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://helloproject.com")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--name", default="ハロプロ", help="通知の見出しに付けるサイト名")
    ap.add_argument("--force", action="store_true", help="versionDir が同じでも比較する")
    args = ap.parse_args()
    global BASE
    BASE = args.base.rstrip("/")

    prev = None
    if os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as f:
            prev = json.load(f)

    version, years = fetch_version(args.base)
    if prev and prev.get("versionDir") == version and not args.force:
        print(f"no change: {version}")
        return

    schedules, artists = fetch_data(args.base, version, years)
    cur = build_snapshot(schedules, artists)
    cur["versionDir"] = version

    if prev:
        msgs = build_messages(prev, cur)
        if not msgs:
            # 状態を書かない＝コミットも発生しない（versionDir は中身が同じでも頻繁に変わる）
            print(f"no content diff ({version})")
            return
        notify(msgs, args.name)
    else:
        print(f"initialized: singles={len(cur['singles'])} regulars={len(cur['regulars'])} events={len(cur['events'])}")

    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
