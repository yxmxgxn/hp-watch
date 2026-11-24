"""公式サイト（ハロプロ・JPROOM・アップフロント）のJSONを取って、使いやすい形にする（rss.py が使う）

公式は、/schedule/（無ければ /news/）のHTMLに版（versionDir）を入れていて、
/json/{versionDir}/{年}_news_list.json・{年}_schedules.json・artist_list.json を配る。

以前は、ここでスケジュールの差分を Discord に通知していたが、やめた（2026-11-24）。
通知（hpwatch.yml）とプロフィールの差分（profiles.py）は、git の履歴に残っている。
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (hp-watch; personal schedule diff)"

# schedule.label がこれに当たるものは定期番組扱い（毎週の放送分は、新しい予定として出さない）
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
