"""メンバーのプロフィール差分ウォッチャー（ハロプロ公式 / J.P ROOM 公式）

流れ:
  1. /schedule/ の artist_list.json からグループ・メンバーの slug を集める
  2. /{slug}/ を取る。メンバーのページならそのまま、グループのページなら /{slug}/{member}/ のリンクを辿る
  3. 各ページの MemberHeader（dt=項目名 / dd=値）を項目の辞書にして state に保存
  4. 前回との差分（追加 / 変更 / 削除、ページの新規 / 消滅 / 移動）を Discord に通知

使い方:
  python profiles.py --base https://helloproject.com --state state/profiles_helloproject.json --name ハロプロ
  python profiles.py --parse page.html     # 1ページだけパースして中身を表示（動作確認用）
初回は状態を作るだけで通知しない。項目が「ない」ことは普通の状態として扱い、
後から項目が増えた / 消えたことは変更として通知する。
メンバーカラーは色名（文字）と色コード（CSS）を別の項目として持つ。
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error

import hpwatch
from hpwatch import get, send_embeds, GREEN, YELLOW, RED

SLEEP = 0.5  # ページ間の待ち（秒）
# グループのページにあるリンクのうち、メンバーではないもの
NOT_MEMBER = {"event", "news", "live", "release", "schedule", "regular", "video", "discography",
              "member", "profile", "movie", "photo", "goods", "fanclub", "special", "radio", "tv"}
ABSENT = "（なし）"


# ---------- 取得 ----------

class FetchError(Exception):
    pass


def fetch(url):
    """HTML を返す。404 は None（ページがない）。それ以外の失敗は FetchError（一時的な不調かもしれない）"""
    last = None
    for attempt in range(3):
        try:
            return get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


# ---------- パース ----------

def clean(frag):
    frag = re.sub(r"<br\s*/?>", "\n", frag)
    frag = html.unescape(re.sub(r"<[^>]+>", "", frag))
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in frag.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def parse_profile(page):
    """プロフィールページのHTMLから {項目名: 値} を作る。メンバーのページでなければ None"""
    page = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.S)
    if 'class="MemberHeader' not in page:
        return None
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if not h1:
        return None
    fields = {"名前": clean(h1.group(1))}

    # h1 の直前: 肩書き（リーダー等）、直後: 読み
    before = page[:h1.start()]
    m = re.search(r'<div class="paragraph-sm mb-1\.5[^"]*">([^<]*)</div>\s*$', before)
    if m and clean(m.group(1)):
        fields["肩書き"] = clean(m.group(1))
    m = re.match(r"\s*<div[^>]*>([^<]*)</div>", page[h1.end():])
    if m and clean(m.group(1)):
        fields["読み"] = clean(m.group(1))

    # 所属: og:title が「名前 - 所属 - サイト名」のとき。「'26」のような年の付き替えは無視する
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    if m:
        parts = html.unescape(m.group(1)).split(" - ")
        if len(parts) == 3:
            fields["所属"] = re.sub(r"\s*['’‘]\d{2}$", "", parts[1]).strip()

    for dl in re.finditer(r'<dl class="MemberHeader__detail">(.*?)</dl>', page, re.S):
        dt = re.search(r"<dt[^>]*>(.*?)</dt>", dl.group(1), re.S)
        dd = re.search(r"<dd([^>]*)>(.*?)</dd>", dl.group(1), re.S)
        if not dt or not dd:
            continue
        key = clean(dt.group(1))
        if not key:
            continue
        fields[key] = clean(dd.group(2))
        if key == "メンバーカラー":  # 色コードは別項目（名前が同じでも CSS が変わることがある）
            c = re.search(r"background(?:-color)?:\s*([^;\"']+)", dd.group(1))
            if c:
                fields["メンバーカラー(色コード)"] = c.group(1).strip().lower()
    return fields


# ---------- 巡回 ----------

def candidate_slugs(artists):
    """artist_list.json から、グループ / メンバーのトップになりそうなパスを集める"""
    paths = []
    for p in (artists.get("profilesById") or {}).values():
        if p.get("slug"):
            paths.append(f"/{p['slug']}/")
        for k in ("url", "path", "link", "href"):
            v = p.get(k)
            if isinstance(v, str) and v.startswith("/") and v.count("/") >= 2:
                paths.append(v if v.endswith("/") else v + "/")
    return list(dict.fromkeys(paths))


def crawl(base, artists):
    """{パス: フィールド辞書} と、取得に失敗したパスの集合を返す"""
    found, failed = {}, set()
    seen_pages = {}

    def visit(path):
        if path in seen_pages:
            return seen_pages[path]
        time.sleep(SLEEP)
        try:
            seen_pages[path] = fetch(base + path)
        except FetchError as e:
            print("fetch failed:", e, file=sys.stderr)
            failed.add(path)
            seen_pages[path] = None
        return seen_pages[path]

    children = []
    for path in candidate_slugs(artists):
        page = visit(path)
        if not page:
            continue
        fields = parse_profile(page)
        if fields:
            found[path] = fields
            continue
        host = re.escape(base)
        for m in re.finditer(r'href="(?:%s)?(%s[a-z0-9_.-]+/)"' % (host, re.escape(path)), page):
            child = m.group(1)
            if child.rstrip("/").rsplit("/", 1)[-1] not in NOT_MEMBER:
                children.append(child)
    for path in dict.fromkeys(children):
        if path in found:
            continue
        page = visit(path)
        fields = parse_profile(page) if page else None
        if fields:
            found[path] = fields
    return found, failed


# ---------- 差分 ----------

def diff_fields(old, new):
    out = []
    for k in list(new) + [k for k in old if k not in new]:
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append((k, a, b))
    return out


def one_line(v):
    return ABSENT if v is None else (v.replace("\n", " / ") or ABSENT)


def build_embeds(prev, cur, site, base):
    embeds = []

    def emb(title, lines, color, path):
        embeds.append({"title": title[:256], "description": "\n".join(lines)[:4000], "color": color,
                       "url": base + path, "footer": {"text": site}})

    added = {p: f for p, f in cur.items() if p not in prev}
    removed = {p: f for p, f in prev.items() if p not in cur}
    # 同じ名前のページが消えて現れた＝ページの移動（グループ移籍・卒業など）
    moved = []
    for rp, rf in list(removed.items()):
        for ap, af in list(added.items()):
            if rf.get("名前") == af.get("名前"):
                moved.append((rp, ap, rf, af))
                del removed[rp], added[ap]
                break

    for rp, ap, rf, af in moved:
        lines = [f"ページ: {rp} → {ap}"] + [f"{k}: {one_line(a)} → {one_line(b)}" for k, a, b in diff_fields(rf, af)]
        emb(f"【ページ移動】{af.get('名前')}", lines, YELLOW, ap)
    for p, f in added.items():
        lines = [f"{k}: {one_line(v)}" for k, v in f.items() if k != "名前"]
        emb(f"【新規ページ】{f.get('名前')}", lines, GREEN, p)
    for p, f in removed.items():
        emb(f"【ページ消滅】{f.get('名前')}", [f"{k}: {one_line(v)}" for k, v in f.items() if k != "名前"], RED, p)
    for p, f in cur.items():
        if p in prev:
            d = diff_fields(prev[p], f)
            if d:
                name = f.get("名前") or prev[p].get("名前")
                emb(f"【プロフィール変更】{name}", [f"{k}: {one_line(a)} → {one_line(b)}" for k, a, b in d], YELLOW, p)
    return embeds


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://helloproject.com")
    ap.add_argument("--state", default="state/profiles.json")
    ap.add_argument("--name", default="ハロプロ", help="通知の見出しに付けるサイト名")
    ap.add_argument("--parse", help="HTMLファイルを1つパースして表示するだけ")
    args = ap.parse_args()

    if args.parse:
        with open(args.parse, encoding="utf-8") as f:
            print(json.dumps(parse_profile(f.read()), ensure_ascii=False, indent=2))
        return

    base = args.base.rstrip("/")
    prev = None
    if os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as f:
            prev = json.load(f)

    version, _ = hpwatch.fetch_version(base)
    artists = json.loads(get(f"{base}/json/{version}/artist_list.json"))
    cur, failed = crawl(base, artists)

    if prev:
        # 取得に失敗したページ（とその配下）は「消えた」ではなく前回のままにする
        for p, f in prev.items():
            if p not in cur and any(p == x or p.startswith(x) for x in failed):
                cur[p] = f
        # 大量に消えたらサイト側の構造変更を疑って、保存も通知もしない
        if len(cur) < len(prev) * 0.5:
            sys.exit(f"取れたページが急減 ({len(prev)} → {len(cur)})。サイト構造の変更？ 状態は更新しない")
    if not cur:
        sys.exit("プロフィールが1件も取れなかった")

    if prev:
        embeds = build_embeds(prev, cur, args.name, base)
        if not embeds:
            print(f"no profile diff ({len(cur)}件)")
            return
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook:
            send_embeds(hook, embeds, args.name, "プロフィール")
        else:
            print(f"=== {args.name}: {len(embeds)}件 ===")
            for e in embeds:
                print(e["title"], e["url"], e["description"], sep="\n", end="\n\n")
    else:
        print(f"initialized: {len(cur)}件")

    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
