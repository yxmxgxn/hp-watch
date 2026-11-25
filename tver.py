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
    """検索結果の1行（リンクの文字）→ {href, title, station, month, day}。放送日の行が無ければ month=None"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    row = {"href": href, "title": lines[0] if lines else "", "station": "", "month": None, "day": None}
    for l in lines[1:]:
        m = AIRED.search(l)
        if m:
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


RULE = 2   # 選び方の版（1: 前後1日も exact。2: 同じ日だけ exact）。版が違う分は、1回、探し直す
LABEL_EXACT = "TVer"
LABEL_NEAR = "TVer（近い日の回）"   # 同じ日の回ではない（読む側にも分かる名前にする）
LEGACY_MAX = 6  # 1回の実行で、昔の検索のページを、番組のページに直す番組の数の上限


def tver_title_matches(title, program):
    """検索結果の番組名が、番組名と合うか（pick と同じ決め方）"""
    t, want = norm(title), norm(program)
    return bool(t and want and (t == want or (len(want) >= 4 and want in t) or (len(t) >= 4 and t in want)))


def pick_match(rows, program, air, back=0):
    """番組名が合い、放送日が合うエピソード (href, exact)。無ければ (None, False)。
    **同じ日**の回だけが exact（True）。前後1日の回（深夜の日付のずれ・TVer より後に放送する局）と、back があれば（毎週の番組。地方局の遅れ放送）
    放送日の back 日前までの、いちばん新しい回は、「近い日の回」（False。本当の回が上がったら、差し替える）。
    前後1日で迷ったら、TVer に先に上がっている（前の日の）回を選ぶ"""
    want = norm(program)
    best = None
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
            best = (rank, r["href"])
    return (best[1], best[0][0] == 0) if best else (None, False)


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


def weekly_tv_items(today):
    """毎週・毎月のテレビ番組の、放送日が今日から3日前までの1回分。公式のデータを（rss.py と同じ作りで）読む"""
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
            if not (today - dt.timedelta(days=DAYS_AFTER) <= air <= today):
                continue
            station, prog = rss.split_station("TV", hpwatch.text_of(x.get("title")).strip())
            if prog:
                guid = f"{site}-media-{rss.media_keys(x)[0]}"
                found[guid] = {"guid": guid, "air": air, "station": station, "program": prog, "weekly": True}
    return list(found.values())


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


def make_entry(it, href, exact, today):
    """自動の分の1件。同じ日の回は「TVer」、近い日の回は「TVer（近い日の回）」（match で、探し直す・直す入り口にも使う）"""
    return {
        "guid": it["guid"], "air": str(it["air"]), "until": str(it["air"] + dt.timedelta(days=DAYS_AFTER)),
        "links": [{"label": LABEL_EXACT if exact else LABEL_NEAR, "url": EPISODE + href}], "source": "auto", "found": today.isoformat(),
        "match": "exact" if exact else "near", "rule": RULE,
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
    todo += [it for it in weekly_tv_items(today) if it["guid"] not in known and it["guid"] not in seen]
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
                for it in its:
                    href, exact = pick_match(rows, prog, it["air"], back=WEEKLY_BACK if it.get("weekly") else 0)
                    if rows:
                        searched.add(it["guid"])
                    if href:
                        first_href = first_href or href
                        found[it["guid"]] = make_entry(it, href, exact, today)
                        print(f"  {prog} {it['air']}: {EPISODE + href}{'' if exact else '（近い日の回。本当の回が上がったら差し替える）'}")
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
