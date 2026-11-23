"""TVer のエピソードのリンクを、放送のあとに自動で探して manual/auto.json に書く

  python tver.py [--state-dir state] [--out manual/auto.json] [--max-search 25] [--list]

放送日から3日（手で入れる分と同じ期限）のテレビの予定について、番組名で TVer を検索し、
「番組名が合う」「放送日が合う（前後1日まで）」エピソードのリンクを入れる。見つからなければ何も入れない（次の実行でまた探す）。
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

JST = dt.timezone(dt.timedelta(hours=9))
DAYS_AFTER = 3            # 放送日から何日まで（手で入れる分と同じ）
SEARCH = "https://tver.jp/search/"
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


def pick(rows, program, air):
    """番組名が合い、放送日が合う（前後1日まで）エピソードの href。日が近い方を選ぶ。無ければ None"""
    want = norm(program)
    best = None
    for r in rows:
        t = norm(r["title"])
        if not t or not want or r["month"] is None:
            continue
        if not (t == want or (len(want) >= 4 and want in t) or (len(t) >= 4 and t in want)):
            continue
        d = nearest(air, r["month"], r["day"])
        diff = abs((d - air).days) if d else 99
        if diff <= 1 and (best is None or diff < best[0]):
            best = (diff, r["href"])
    return best[1] if best else None


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


def merge_auto(old, found, today):
    """今の自動の分に、見つけた分を足す。期限の切れた分は外す。戻り値: 項目のリスト（放送日順）"""
    keep = {g: e for g, e in old.items() if str(e.get("until") or "") >= today.isoformat()}
    keep.update(found)
    return sorted(keep.values(), key=lambda e: (str(e.get("air")), e["guid"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--manual", default="manual/import.json")
    ap.add_argument("--out", default="manual/auto.json")
    ap.add_argument("--max-search", type=int, default=25)
    ap.add_argument("--list", action="store_true", help="探す項目を出すだけ（ブラウザは使わない）")
    args = ap.parse_args()

    today = dt.datetime.now(JST).date()
    manual = load_entries(args.manual, today)
    auto = load_entries(args.out, today)
    known = {g for g, e in {**auto, **manual}.items() if has_link(e)}
    todo = items_to_check(args.state_dir, today, known)
    programs = {}
    for it in todo:
        programs.setdefault(it["program"], []).append(it)
    print(f"探す項目 {len(todo)} 件・番組 {len(programs)} 本（今日 {today}）")
    if args.list:
        for prog, its in programs.items():
            print("  ", prog, [str(i["air"]) for i in its])
        return

    found = {}
    if programs:
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
                for it in its:
                    href = pick(rows, prog, it["air"])
                    if href:
                        found[it["guid"]] = {
                            "guid": it["guid"], "air": str(it["air"]), "until": str(it["air"] + dt.timedelta(days=DAYS_AFTER)),
                            "links": [{"label": "TVer", "url": EPISODE + href}], "source": "auto", "found": today.isoformat(),
                        }
                        print(f"  {prog} {it['air']}: {EPISODE + href}")
                    else:
                        print(f"  {prog} {it['air']}: 見つからない（{len(rows)} 件の結果）")
                time.sleep(2)
            browser.close()

    items = merge_auto(auto, found, today)
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
