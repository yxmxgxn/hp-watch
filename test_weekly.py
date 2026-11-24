"""毎週の番組の取り出しと、最初から入れておくリンクの確かめ（ネットワークなし）。  python3 test_weekly.py"""
import json
import os
import tempfile

import rss

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise SystemExit(f"NG: {msg}")
    ok += 1
    print("ok:", msg)


def item(title, cat, date, label="", start="24:00"):
    return {"category": cat, "date": date, "title": {"content": title}, "schedule": {"start": start, "label": label}, "link": {"link": ""}}


tv_weekly = item("テレビ東京系「Hello! Music Lab。」", "TV", "2026-11-23", "毎週月曜日", "25:00")
tv_single = item("日本テレビ「ベストアーティスト2026」", "TV", "2026-11-28", "", "16:00")
mag_monthly = item("東京ニュース通信社「B.L.T.」連載「ハロラボ!!」", "MAGAZINE", "2026-11-28", "毎月28日", "")
other = item("イベント", "EVENT", "2026-11-23", "毎週月曜日")
xs = [tv_weekly, tv_single, mag_monthly, other]
check([x["title"]["content"] for x in rss.weekly_candidates(xs, {})] == [tv_weekly["title"]["content"], mag_monthly["title"]["content"]], "毎週・毎月の番組を取り出す（単発・MEDIA 以外は入らない）")
check([x["title"]["content"] for x in rss.media_candidates(xs, {})] == [tv_single["title"]["content"]], "MEDIA には、単発だけ（毎週は入らない）")

# 同じ予定は、MEDIA と同じ形の guid になる（手で入れた分・自動の分と同じ仕組みで結べる）
check(rss.media_keys(tv_weekly)[0].startswith("h") and len(rss.media_keys(tv_weekly)[0]) == 17, "予定のキーは h + 16文字")


class FakeRk:
    def station_ids(self, title):
        return ["TBS"] if "TBSラジオ" in title else []

    def name(self, sid):
        return "TBSラジオ"


radio = item("TBSラジオ「Music Palette♪」", "RADIO", "2026-11-22", "毎週日曜日", "04:00")
progs = {rss.program_key("TV", "Hello! Music Lab。"): {"name": "Hello! Music Lab。", "tver": "https://tver.jp/search/x"},
         rss.program_key("RADIO", "Music Palette♪"): {"name": "Music Palette♪", "spotify": "https://open.spotify.com/show/abc"}}
sl = rss.standing_links("RADIO", "Music Palette♪", [radio], FakeRk(), progs)
check(sl[0][1] == "https://radiko.jp/#!/live/TBS" and "生放送" in sl[0][0], "ラジオは、radiko の生放送を、最初から入れる")
check(("Spotify（番組）", "https://open.spotify.com/show/abc") in sl, "Spotify の番組は、ポッドキャストとは別の欄で、最初から入れる")
check(rss.standing_links("TV", "Hello! Music Lab。", [tv_weekly], FakeRk(), progs) == [("TVer（番組）", "https://tver.jp/search/x")], "テレビは、TVer にある番組だけ、番組のページを入れる")
check(rss.standing_links("TV", "NHKの番組", [tv_weekly], FakeRk(), progs) == [], "TVer に無い番組には、何も入れない")
check(rss.standing_links("MAGAZINE", "B.L.T.", [mag_monthly], FakeRk(), progs) == [], "雑誌には、何も入れない")

# 番組の記録が無ければ、空
rss.PROGRAMS_PATH = os.path.join(tempfile.mkdtemp(), "none.json")
check(rss.load_programs() == {}, "manual/programs.json が無ければ、空")

# トップページ: フィードごとの、コピーと確かめる（最新5件）のボタン
import datetime as dt
fx = [{"site": "hp", "kind": "media", "name": "全体", "url": "https://example.test/hp/media/all.xml"},
      {"site": "hp", "kind": "today", "name": "全体", "url": "https://example.test/hp/today.xml"}]
page = rss.index_html(fx, dt.datetime(2026, 11, 24, 12, 0, tzinfo=rss.JST))
check(page.count('<li class="feed">') == 2 and page.count('class="cp"') == 2 and page.count('class="ck"') == 2, "トップページ: フィードごとに、コピーと確かめるのボタン")
check('data-url="https://example.test/hp/today.xml"' in page and "<ol class=\"chk\" hidden>" in page, "トップページ: ボタンは、そのフィードのURLを持つ。5件の一覧は、押すまで隠れている")
check("slice.call(items, 0, 5)" in page and "textContent" in page and "innerHTML" not in page, "トップページ: 最新5件を、textContent で並べる（フィードの中身を HTML として入れない）")

print(f"\nall {ok} ok")
