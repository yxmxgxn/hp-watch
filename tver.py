"""TVer のエピソードのリンクを、放送のあとに自動で探して manual/auto.json に書く

  python tver.py [--state-dir state] [--out manual/auto.json] [--max-search 25] [--list]

放送日から3日（手で入れる分と同じ期限）のテレビの予定（新しく載った単発の予定と、毎週・毎月の番組の1回分）について、番組名で TVer を検索し、
「番組名が合う」「放送日が合う」エピソードのリンクを入れる。見つからなければ何も入れない（次の実行でまた探す）。
放送日が合う: 前後1日まで。毎週の番組は、地方局の遅れ放送もあるので、放送日の8日前までのエピソードのうち、いちばん新しいもの。
TVer にある番組が見つかったら、その番組の TVer の検索のページを manual/programs.json に残す（毎週の番組に、放送の前から入れておくリンク）。
- 検索は、番組名ごとに1回（同じ番組の別の日は、まとめて1回）。1回の実行で最大 max-search 回。間は2秒あける。画像・フォントは読まない
- 取るのは、エピソードのURLと、結果の行の番組名・放送局・放送日だけ（説明・画像・サムネイルは取らない）
- 手で入れた分（manual/import.json）が優先。手で入れた分にリンクが無いときだけ、rss.py が自動の分を補う
- 検索のページは JavaScript で描かれるので、ブラウザ（Playwright）で読む。--list は、探す項目を出すだけ（ブラウザは使わない）
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import time
import unicodedata
import urllib.parse

import hpwatch
import rss

JST = dt.timezone(dt.timedelta(hours=9))
DAYS_AFTER = 3            # 放送日から何日まで（手で入れる分と同じ）
SEARCH = "https://tver.jp/search/"
SERIES = "https://tver.jp/series/"
EPISODE = "https://tver.jp"
PROGRAM = re.compile(r"^\s*(?P<station>.*?)\s*「(?P<program>.*)」\s*$")  # 局名「番組名」
AIRED = re.compile(r"(?P<station>\S+)\s+(?P<m>\d{1,2})月(?P<d>\d{1,2})日\([^)]*\)放送分")  # 局 11月21日(土)放送分
ENDS = re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日\([^)]*\)\s*(?P<h>\d{1,2}):(?P<mi>\d{2})\s*終了予定")  # 12月1日(火)0:58 終了予定
# 2026年11月から、TVer の検索結果の書式が変わった（1行目に「21分」・放送日は「11/25(水)放送 TBS」・終了は「12/3(木)0:57 終了」「終了間近」「終了まで1週間以上」）。古い書式も読む
DURATION = re.compile(r"^(?:\d+時間)?(?:\d+分)?$")
AIRED_NEW = re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})\([^)]*\)(?:放送\s+|\s+\d{1,2}:\d{2}\s+)(?P<station>\S.*)$")   # 11/25(水)放送 TBS ・ 11/23(月) 20:09 山形放送
ENDS_NEW = re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})\([^)]*\)\s*(?P<h>\d{1,2}):(?P<mi>\d{2})\s*終了$")   # 12/3(木)0:57 終了
ENDS_IN = re.compile(r"^終了まで(?P<n>\d+)時間")   # 終了まで3時間（「終了まで1週間以上」「終了まで3日」は、まだ先）


def norm(s):
    """全角・半角・大文字・小文字・空白・記号をそろえる"""
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", s or "").casefold())


def parse_program(name):
    """「HBCテレビ「キタコイ」」→ ("HBCテレビ", "キタコイ")。形が違えば None"""
    m = PROGRAM.match(name or "")
    if not m or not m.group("program").strip():
        return None
    return m.group("station").strip(), m.group("program").strip()


def parse_row(href, text):
    """検索結果の1行（リンクの文字）→ {href, title（番組名）, sub（エピソードの題名）, station, month, day, ends?, ends_soon?}。放送日の行が無ければ month=None"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    if len(lines) > 1 and DURATION.match(lines[0]):
        lines = lines[1:]   # 1行目の「21分」（長さ）
    row = {"href": href, "title": lines[0] if lines else "", "sub": "", "station": "", "month": None, "day": None}

    def is_meta(l):
        return bool(AIRED.search(l) or AIRED_NEW.match(l) or ENDS_NEW.match(l) or l.startswith("終了"))

    if len(lines) > 1 and not is_meta(lines[1]):
        row["sub"] = lines[1]
    for l in lines[1:]:
        m = ENDS.search(l) or ENDS_NEW.match(l)
        if m:
            row["ends"] = (int(m.group("m")), int(m.group("d")), int(m.group("h")), int(m.group("mi")))
            break
        if l.startswith("終了間近"):
            row["ends_soon"] = True
            break
        m = ENDS_IN.match(l)
        if m:
            if int(m.group("n")) < SOON_HOURS:
                row["ends_soon"] = True
            break
    for l in lines[1:]:
        m = AIRED.search(l) or AIRED_NEW.match(l)
        if m and m.group("station") != "終了":
            row.update(station=m.group("station"), month=int(m.group("m")), day=int(m.group("d")))
            break
    return row


def nearest(air, month, day):
    """月日から、放送日（air）にいちばん近い日付（年をまたぐ12月・1月のため）"""
    best = None
    for y in (air.year - 1, air.year, air.year + 1):
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - air).days) < abs((best - air).days):
            best = d
    return best


WEEKLY_BACK = 6  # 毎週の番組の、遅れ放送を拾う日数。前の週の同じ回は7日前なので、含めない（前の週の回は、公開期間が切れて、見られない）


RULE = 4   # 選び方の版（1: 前後1日も exact。2: 同じ日だけ exact。3: リンクに、どの回か〔ep〕を持つ。4: 毎週の番組は、最速の局の放送の日時で回を決める・先週分）。版が違う分は、1回、探し直す
LABEL_EXACT = "TVer"
LABEL_NEAR = "TVer（近い日の回）"   # 同じ日の回ではない（読む側にも分かる名前にする）
LABEL_PREV = "TVer（先週分）"       # 今週の回がまだ TVer に無い。直前の回（まだ見られる回）
LABEL_PREV_LONG = "TVer（前の回）"  # 直前の回が、10日以上前
PREV_LONG = 10
LEGACY_MAX = 6  # 1回の実行で、昔の検索のページを、番組のページに直す番組の数の上限


def tver_title_matches(title, program):
    """検索結果の番組名が、番組名と合うか（pick と同じ決め方）"""
    t, want = norm(title), norm(program)
    return bool(t and want and (t == want or (len(want) >= 4 and want in t) or (len(t) >= 4 and t in want)))


def pick_row(rows, program, air, back=0):
    """番組名が合い、放送日が合うエピソード (href, exact)。無ければ (None, False)。
    **同じ日**の回だけが exact（True）。前後1日の回（深夜の日付のずれ・TVer より後に放送する局）と、back があれば（毎週の番組。地方局の遅れ放送）
    放送日の back 日前までの、いちばん新しい回は、「近い日の回」（False。本当の回が上がったら、差し替える）。
    前後1日で迷ったら、TVer に先に上がっている（前の日の）回を選ぶ"""
    want = norm(program)
    best = None   # (rank, row, 回の日付)
    for r in rows:
        t = norm(r["title"])
        if not t or not want or r["month"] is None:
            continue
        if not (t == want or (len(want) >= 4 and want in t) or (len(t) >= 4 and t in want)):
            continue
        d = nearest(air, r["month"], r["day"])
        if d is None:
            continue
        diff = (air - d).days  # 正: エピソードのほうが前
        if -1 <= diff <= 1:
            rank = (abs(diff), 0 if diff >= 0 else 1)   # 同じ日 → 前の日 → 次の日
        elif 1 < diff <= back:
            rank = (10 + diff, 0)
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, r, d)
    return (best[1], best[2], best[0][0] == 0) if best else (None, None, False)


def pick_match(rows, program, air, back=0):
    """pick_row の (href, exact)"""
    row, _d, exact = pick_row(rows, program, air, back)
    return (row["href"] if row else None, exact)


def air_time(date, start):
    """放送の日時（日本時間・タイムゾーン無し）。開始は "24:59" のように24時を超える（翌日の0:59）。開始が読めなければ None"""
    m = re.match(r"^(\d{1,2}):(\d{2})", start or "")
    if not m:
        return None
    return dt.datetime.combine(date, dt.time()) + dt.timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))


def row_ends(row, ref):
    """検索結果の行の、配信の終了予定（日本時間）。無ければ None（「配信終了まで1週間以上」など）"""
    e = row.get("ends")
    if not e:
        return None
    d = nearest(ref, e[0], e[1])
    return dt.datetime.combine(d, dt.time(e[2], e[3])) if d else None


def same_station(a, b):
    """同じ局か（そろえた名前が、一方がもう一方を含む）。BS の局（BSテレ東）と、地上波の局（テレ東）は、別の局"""
    x, y = norm(a), norm(b)
    if not (x and y):
        return False
    if x.startswith("bs") != y.startswith("bs"):
        return False
    return x in y or y in x


PREV_GAP = 6.5   # 放送が、その番組の最新の回の最初の放送から、これ以上（日）あとなら、今週の回がまだ上がっていない（最新の回は、先週分）
SOON_HOURS = 12
SOON = dt.timedelta(hours=SOON_HOURS)   # 配信が、これより早く終わる回は、選ばない（開いたら、見られない）


def cycle_pick(rows, program, occ, ctx, now):
    """毎週の番組の1回分 occ（air・start・station）に、時刻で回を決める。戻り値: (行, 回の日付, exact, gap日) または None。
    TVer の行の局が、その回を最初に放送する局（最速の局）。その局の放送の日時（ctx＝同じ番組の1回分の一覧の中の、同じ局・回の日付の前後1日）を、
    その回の最初の放送とする（無ければ、回の日付の0時）。occ の放送が、その日時より前なら、1つ前の回にする（最速の局より前の放送は、前の回）。
    後なら、いちばん新しい、始まった回。配信がまもなく終わる回は選ばない。exact は、最速の局そのもの、または、回と同じ日の放送"""
    t = air_time(occ["air"], occ.get("start"))
    if t is None:
        return None
    best = None
    for r in rows:
        if r["month"] is None or not tver_title_matches(r["title"], program):
            continue
        ends = row_ends(r, occ["air"])
        if r.get("ends_soon") or (ends and ends < now + SOON):
            continue
        d = nearest(occ["air"], r["month"], r["day"])
        if d is None:
            continue
        key = None
        for o in ctx:
            if o["program"] != occ["program"] or not same_station(o["station"], r["station"]):
                continue
            ot = air_time(o["air"], o.get("start"))
            if ot is None or abs((o["air"] - d).days) > 1:
                continue
            if key is None or abs((o["air"] - d).days) < abs((key[0] - d).days):
                key = (o["air"], ot)
        first = key[1] if key else dt.datetime.combine(d, dt.time())
        if first > t:
            continue   # この回の最初の放送より前の放送は、この回ではない
        if best is None or first > best[0]:
            best = (first, r, d)
    if not best:
        return None
    first, r, d = best
    exact = same_station(occ["station"], r["station"]) and abs((occ["air"] - d).days) <= 1 or occ["air"] == d
    return r, d, exact, (t - first).total_seconds() / 86400


def pick(rows, program, air, back=0):
    """pick_match の href だけ"""
    return pick_match(rows, program, air, back)[0]


def load_entries(path, today):
    """期限内の項目（{guid: 項目}）。ファイルが無ければ空"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        items = (json.load(f) or {}).get("items") or []
    return {e["guid"]: e for e in items if e.get("guid") and str(e.get("until") or "") >= today.isoformat()}


def has_link(e):
    return bool(e.get("links") or e.get("tver"))


def items_to_check(state_dir, today, known):
    """探す項目: テレビ・放送日が今日から3日前までの間・まだリンクが無い・番組名が取れるもの"""
    found = {}
    for f in sorted(glob.glob(os.path.join(state_dir, "rss_media_*.json"))):
        with open(f, encoding="utf-8") as fh:
            items = (json.load(fh) or {}).get("items") or []
        for i in items:
            cats = i.get("cats") or i.get("categories") or []
            if not cats or cats[0] != "TV" or i["guid"] in known:
                continue
            try:
                air = dt.date.fromisoformat(str(i.get("sort") or "")[:10])
            except ValueError:
                continue
            if not (today - dt.timedelta(days=DAYS_AFTER) <= air <= today):
                continue
            parsed = parse_program(i.get("name") or i.get("title") or "")
            if parsed:
                found[i["guid"]] = {"guid": i["guid"], "air": air, "station": parsed[0], "program": parsed[1]}
    return list(found.values())


def weekly_tv_items(today, back=14):
    """毎週・毎月のテレビ番組の、放送日が今日の back 日前から明日までの1回分（探すのは、そのうち3日前までの分。ほかは、最速の局の放送の日時を知るのに使う）。
    公式のデータを（rss.py と同じ作りで）読む"""
    found = {}
    for site, cfg in rss.SITES.items():
        try:
            version, years = hpwatch.fetch_version(cfg["base"])
            schedules, artists = hpwatch.fetch_data(cfg["base"], version, years)
        except Exception as e:  # 1つのサイトの失敗で、全体を止めない
            print(f"  {site}: 公式のデータが取れない {str(e)[:60]}")
            continue
        profiles = artists.get("profilesById") or {}
        for x in rss.weekly_candidates(schedules, profiles):
            if x.get("category") != "TV":
                continue
            try:
                air = dt.date.fromisoformat(x["date"])
            except ValueError:
                continue
            if not (today - dt.timedelta(days=back) <= air <= today + dt.timedelta(days=1)):
                continue
            station, prog = rss.split_station("TV", hpwatch.text_of(x.get("title")).strip())
            if prog:
                guid = f"{site}-media-{rss.media_keys(x)[0]}"
                s_ = x.get("schedule") or {}
                found[guid] = {"guid": guid, "air": air, "station": station, "program": prog, "weekly": True,
                               "start": s_.get("start") or "", "label": s_.get("label") or ""}
    return list(found.values())


def no_tver_programs(path):
    """programs.json で、TVer に無い番組（no_tver）の、そろえた番組名の集まり。探さない"""
    try:
        with open(path, encoding="utf-8") as f:
            progs = (json.load(f) or {}).get("programs", {})
    except (OSError, ValueError):
        return set()
    return {norm(p.get("name")) for p in progs.values() if p.get("no_tver") and p.get("name")}


def has_series(path, prog):
    """programs.json に、番組のページ（series）が、すでにあるか"""
    try:
        with open(path, encoding="utf-8") as f:
            cur = (json.load(f) or {}).get("programs", {}).get(rss.program_key("TV", prog)) or {}
    except (OSError, ValueError):
        return False
    return str(cur.get("tver") or "").startswith(SERIES)


def legacy_search_programs(path):
    """programs.json で、tver が昔の検索のページのままの番組名（番組のページに差し替える対象）"""
    try:
        with open(path, encoding="utf-8") as f:
            progs = (json.load(f) or {}).get("programs", {})
    except (OSError, ValueError):
        return []
    return sorted(p.get("name") for p in progs.values() if p.get("name") and str(p.get("tver") or "").startswith(SEARCH))


def update_programs(path, series_by_prog):
    """TVer にある番組の、番組のページ（series）を残す。{番組名: series の URL または None}。ほかの欄は、そのまま。変わったら True。
    番組のページは、TVer にある番組だけに付ける（検索のページは、TVer に無い番組と区別がつかないので、使わない）。
    すでに番組のページ（手で入れたものを含む）があれば、そのまま。昔の検索のページは、番組のページに差し替える（取れなければ、外す）"""
    data = {"programs": {}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {"programs": {}}
    progs = data.setdefault("programs", {})
    changed = False
    for prog, series in sorted(series_by_prog.items()):
        key = rss.program_key("TV", prog)
        cur = progs.get(key)
        old = str((cur or {}).get("tver") or "")
        if old.startswith(SERIES):
            continue
        if series:
            cur = progs.setdefault(key, {"name": prog, "cat": "TV"})
            cur["tver"] = series
            changed = True
        elif old and cur is not None:
            del cur["tver"]  # 昔の検索のページ（TVer にあるかどうか、区別がつかない）
            changed = True
    if changed:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return changed


SERIES_RE = re.compile(r"/series/(sr[a-z0-9]+)")


def parse_series(html):
    """エピソードのページの中から、番組のページ（https://tver.jp/series/sr…）。無ければ None"""
    m = SERIES_RE.search(html or "")
    return f"https://tver.jp/series/{m.group(1)}" if m else None


def series_of(page, href):
    """エピソードのページを開いて、番組のページを取る（公開期間外のページには、無い）"""
    page.goto(EPISODE + href, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector('a[href^="/series/"]', timeout=12000)
    except Exception:
        pass
    return parse_series(page.content())


def search_rows(page, program):
    """番組名で検索して、結果の行を返す（取るのは、リンクと、行の文字だけ）"""
    page.goto(SEARCH + program, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector('a[href^="/episodes/"]', timeout=15000)
    except Exception:
        return []  # 結果が無い（TVer に無い番組）か、読み込みが間に合わなかった
    page.wait_for_timeout(1500)
    raw = page.eval_on_selector_all('a[href^="/episodes/"]', "els => els.slice(0, 20).map(a => [a.getAttribute('href'), a.innerText || ''])")
    return [parse_row(h, t) for h, t in raw]


def make_entry(it, href, exact, today, ep_date=None, ep_title="", prev_gap=None):
    """自動の分の1件。同じ日の回は「TVer」、近い日の回は「TVer（近い日の回）」、今週の回がまだ無くて、直前の回を付けた分は「TVer（先週分）」
    （最新の回が、10日以上前なら「TVer（前の回）」）（match で、探し直す・直す入り口にも使う）。
    ep: どの回か（TVer の「◯月◯日放送分」の日付と、エピソードの題名）。手で入れるページが、放送日とのずれを見せるのに使う"""
    if prev_gap is not None:
        label, match = (LABEL_PREV if prev_gap < PREV_LONG else LABEL_PREV_LONG), "prev"
    else:
        label, match = (LABEL_EXACT if exact else LABEL_NEAR), ("exact" if exact else "near")
    link = {"label": label, "url": EPISODE + href}
    if ep_date:
        link["ep"] = {"date": ep_date.isoformat(), "kind": "air", "title": (ep_title or "")[:60]}
    return {
        "guid": it["guid"], "air": str(it["air"]), "until": str(it["air"] + dt.timedelta(days=DAYS_AFTER)),
        "links": [link], "source": "auto", "found": today.isoformat(),
        "match": match, "rule": RULE,
    }


def merge_auto(old, found, today, drop=()):
    """今の自動の分に、見つけた分を足す。期限の切れた分と、drop（探し直して、もう合わなかった分）は外す。戻り値: 項目のリスト（放送日順）"""
    keep = {g: e for g, e in old.items() if str(e.get("until") or "") >= today.isoformat() and g not in drop}
    keep.update(found)
    return sorted(keep.values(), key=lambda e: (str(e.get("air")), e["guid"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--manual", default="manual/import.json")
    ap.add_argument("--out", default="manual/auto.json")
    ap.add_argument("--programs", default="manual/programs.json")
    ap.add_argument("--max-search", type=int, default=25)
    ap.add_argument("--list", action="store_true", help="探す項目を出すだけ（ブラウザは使わない）")
    args = ap.parse_args()

    today = dt.datetime.now(JST).date()
    manual = load_entries(args.manual, today)
    auto = load_entries(args.out, today)
    # 探し終わった項目: 手で入れた分と、放送日が完全に合った自動の分。「近い日の回」（match が exact でない・昔の形で match が無い）は、
    # 本当の回が上がるまで、期限まで探し直す（前の週の回を選んでいた分も、ここで直る）
    settled = lambda e: has_link(e) and e.get("match") == "exact" and e.get("rule") == RULE
    known = {g for g, e in manual.items() if has_link(e)} | {g for g, e in auto.items() if settled(e)}
    recheck = {g for g, e in auto.items() if has_link(e) and not settled(e) and g not in manual}
    todo = items_to_check(args.state_dir, today, known)
    seen = {it["guid"] for it in todo}
    weekly_all = weekly_tv_items(today)   # 前後14日。回の決め方（最速の局の放送の日時）に使う。探すのは、そのうち3日前までの分
    in_window = lambda it: today - dt.timedelta(days=DAYS_AFTER) <= it["air"] <= today
    todo += [it for it in weekly_all if in_window(it) and it["guid"] not in known and it["guid"] not in seen]
    skip = no_tver_programs(args.programs)   # TVer に無い番組（探さない）
    todo = [it for it in todo if norm(it["program"]) not in skip]
    now = dt.datetime.now(JST).replace(tzinfo=None, microsecond=0)
    programs = {}
    for it in todo:
        programs.setdefault(it["program"], []).append(it)
    print(f"探す項目 {len(todo)} 件・番組 {len(programs)} 本（今日 {today}）")
    if args.list:
        for prog, its in programs.items():
            print("  ", prog, [str(i["air"]) for i in its])
        return

    found = {}
    series_by_prog = {}   # 番組名 → 番組のページ（取れなければ None）
    searched = set()      # 検索の結果が読めた項目（読めたうえで合わなければ、昔の分を外す）
    legacy = legacy_search_programs(args.programs)   # 昔の検索のページのままの番組（今日の探す項目に無くても、番組のページに直す）
    if programs or legacy:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # 手元の確認用: TVER_CHROMIUM に Chromium のパスを入れると、それを使う（Actions では、入れたものを使う）
            browser = p.chromium.launch(executable_path=os.environ.get("TVER_CHROMIUM") or None)
            ctx = browser.new_context(locale="ja-JP")
            page = ctx.new_page()
            page.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
            for n, (prog, its) in enumerate(programs.items()):
                if n >= args.max_search:
                    print(f"検索の上限（{args.max_search}）に達したので、残りは次の実行で")
                    break
                try:
                    rows = search_rows(page, prog)
                except Exception as e:  # 1本の失敗で全体を止めない
                    print(f"  {prog}: 失敗 {str(e)[:80]}")
                    continue
                first_href = None
                if rows and not any(r["month"] is not None for r in rows):
                    print(f"  {prog}: 結果は {len(rows)} 件あるのに、放送日が1行も読めない（TVer の書式が変わったかも）。昔の分は外さない")
                for it in its:
                    prev_gap = None
                    if it.get("weekly") and it.get("start"):
                        res = cycle_pick(rows, prog, it, weekly_all, now) if rows else None
                        row, ep_d, exact, gap = res if res else (None, None, False, None)
                        if row and not exact and gap >= PREV_GAP:
                            prev_gap = gap   # 今週の回がまだ上がっていない（月2回・月1回の番組も）。直前の回を、先週分・前の回として付ける
                    else:
                        row, ep_d, exact = pick_row(rows, prog, it["air"], back=WEEKLY_BACK if it.get("weekly") else 0)
                    href = row["href"] if row else None
                    if rows and any(r["month"] is not None for r in rows):
                        searched.add(it["guid"])   # 放送日が1行も読めないときは、TVer の書式が変わったとみて、昔の分を外さない
                    if href:
                        first_href = first_href or href
                        found[it["guid"]] = make_entry(it, href, exact, today, ep_d, row.get("sub", ""), prev_gap)
                        note = "" if exact else "（先週分。今週の回が上がったら差し替える）" if prev_gap is not None else "（近い日の回。本当の回が上がったら差し替える）"
                        print(f"  {prog} {it['air']}: {EPISODE + href}{note}")
                    else:
                        print(f"  {prog} {it['air']}: 見つからない（{len(rows)} 件の結果）")
                if first_href and not has_series(args.programs, prog):
                    # TVer にある番組。番組のページ（series）を、エピソードのページから取る（すでに有れば、取らない）
                    series_by_prog[prog] = None
                    try:
                        time.sleep(2)
                        series_by_prog[prog] = series_of(page, first_href)
                    except Exception as e:
                        print(f"  {prog}: 番組のページが取れない {str(e)[:60]}")
                    print(f"  {prog}: 番組のページ {series_by_prog[prog] or '（取れない。次の実行でまた探す）'}")
                time.sleep(2)
            # 昔の検索のページのままの番組: 検索して、番組名が合う最初の結果のページから、番組のページを取る（取れなければ、検索のページを外す）
            for prog in [p for p in legacy if p not in series_by_prog][:LEGACY_MAX]:
                series_by_prog[prog] = None
                try:
                    rows = search_rows(page, prog)
                    href = next((r["href"] for r in rows if tver_title_matches(r["title"], prog)), None)
                    if href:
                        time.sleep(2)
                        series_by_prog[prog] = series_of(page, href)
                except Exception as e:
                    print(f"  {prog}: 番組のページが取れない {str(e)[:60]}")
                print(f"  {prog}: 番組のページ {series_by_prog[prog] or '（取れない）'}（昔の検索のページから）")
                time.sleep(2)
            browser.close()

    if series_by_prog and update_programs(args.programs, series_by_prog):
        print(f"書いた: {args.programs}（番組のページ {sum(1 for s in series_by_prog.values() if s)} 本）")
    drop = {g for g in recheck if g in searched and g not in found}
    if drop:
        print(f"外す: 探し直して、合う回が無かった自動の分 {len(drop)} 件（前の週の回など）")
    items = merge_auto(auto, found, today, drop)
    text = json.dumps({"items": items}, ensure_ascii=False, indent=1) + "\n"
    old = open(args.out, encoding="utf-8").read() if os.path.exists(args.out) else ""
    if text != old:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"書いた: {args.out}（{len(items)} 件・新しく {len(found)} 件）")
    else:
        print("変更なし")


if __name__ == "__main__":
    main()
