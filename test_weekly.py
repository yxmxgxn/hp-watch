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
check(("Spotify（全エピソード）", "https://open.spotify.com/show/abc") in sl, "Spotify の番組は、ポッドキャストとは別の欄で、最初から入れる")
check(rss.standing_links("TV", "Hello! Music Lab。", [tv_weekly], FakeRk(), progs) == [("TVer（番組）", "https://tver.jp/search/x")], "テレビは、TVer にある番組だけ、番組のページを入れる")
check(rss.standing_links("TV", "NHKの番組", [tv_weekly], FakeRk(), progs) == [], "TVer に無い番組には、何も入れない")
check(rss.standing_links("MAGAZINE", "B.L.T.", [mag_monthly], FakeRk(), progs) == [], "雑誌には、何も入れない")

# 番組の記録が無ければ、空
rss.PROGRAMS_PATH = os.path.join(tempfile.mkdtemp(), "none.json")
check(rss.load_programs() == {}, "manual/programs.json が無ければ、空")

# このサイトについて（about/index.html）
import datetime as dt
t0 = dt.datetime(2026, 11, 24, 12, 0, tzinfo=rss.JST)
about = rss.about_page(t0)
top = rss.index_html([], t0)
check('<a href="about/"><b>このサイトについて</b></a>' in top and "<summary>このサイトについて</summary>" not in top, "トップページ: このサイトについて、への目立つリンク（折りたたみの中ではない）")
check(all(h in about for h in ("どのサイトを読んでいるか", "どういう仕組みか", "どういうフィードか", "選んで作る RSS の、絞り込み", "放送のあとに付くリンク")), "このサイトについて: 読んでいるサイト・仕組み・フィード・絞り込み・放送のあとのリンク")
check("題名にその人の名前があるか" in about and "テーマ（カテゴリ）" in about and "グループ・人のタグ" in about, "このサイトについて: アメブロは名前で絞る（テーマは使わない）・人のタグで分ける")
import re
about_text = re.sub(r"<script>.*?</script>", "", about, flags=re.S)  # 見える文面だけ（フォームのスクリプトは除く）
check("json" not in about_text.lower() and "radio/" not in about_text and "Discord" not in about, "このサイトについて: 内部のファイルの名前・通知の仕組みは出さない")
# ご連絡・更新履歴・フッター
check("<strong>個人が作った非公式のサイト</strong>" in about and "Instagram" not in about and "Meta" not in about and "投稿" not in about_text, "ご連絡: インスタの文面を、このサイト用に直した（インスタ・Meta・投稿の語を出さない）")
check('<form id="td"' in about and 'name="target" required' in about and 'class="hp"' in about and 'fetch("/report"' in about, "ご連絡: フォーム（対象は必須・見えない欄・/report へ送る）")
check('<h3 id="changes">更新履歴</h3>' in about and '<time datetime="2026-11-24">2026/11/24</time>　ひな形完成' in about, "更新履歴: 2026/11/24 ひな形完成")
import selectable
sel_out = tempfile.mkdtemp(); selectable.write_options(sel_out, [])
custom_html = open(os.path.join(sel_out, "custom/index.html"), encoding="utf-8").read()
check(all(('<footer class="site-foot">' in h and 'href="/about/#contact"' in h and "検索に戻る" not in h and "tech" not in h.split("<footer")[1] and "更新履歴" not in h.split("<footer")[1]) for h in (about, rss.index_html([], t0), custom_html)), "フッター: 一覧・このサイトについて・選んで作る RSS のどのページも同じ（インスタ用の『仕組み』『検索に戻る』『更新履歴』のリンクは無い）")
foots = [h.split("<footer")[1] for h in (about, rss.index_html([], t0), custom_html)]
check(all("https://x.com/intent/post?text=" in f and "%E3%83%8F%E3%83%AD%E3%83%97%E3%83%AD" in f and "url=https%3A%2F%2Fhello-rss.karin-archive.com%2F" in f for f in foots), "フッター: X で紹介（題名と、サイトのURLだけ。宣伝の文は入れない）")
check(all("https://social-plugins.line.me/lineit/share?url=https%3A%2F%2Fhello-rss.karin-archive.com%2F" in f and 'data-url="https://hello-rss.karin-archive.com/"' in f and "URLをコピー" in f and 'rel="noopener noreferrer"' in f for f in foots), "フッター: LINE で紹介・URLをコピー（外のページは noopener）")

# トップページ: フィードごとの、コピーと確かめる（最新5件）のボタン
import datetime as dt
fx = [{"site": "hp", "kind": "media", "name": "全体", "url": "https://example.test/hp/media/all.xml"},
      {"site": "hp", "kind": "today", "name": "全体", "url": "https://example.test/hp/today.xml"}]
page = rss.index_html(fx, dt.datetime(2026, 11, 24, 12, 0, tzinfo=rss.JST))
check(page.count('<li class="feed">') == 2 and page.count('class="cp"') == 2 and page.count('class="ck"') == 2, "トップページ: フィードごとに、コピーと確かめるのボタン")
check('data-url="https://example.test/hp/today.xml"' in page and "<ol class=\"chk\" hidden>" in page, "トップページ: ボタンは、そのフィードのURLを持つ。5件の一覧は、押すまで隠れている")
check("slice.call(items, 0, 5)" in page and "textContent" in page and "innerHTML" not in page, "トップページ: 最新5件を、textContent で並べる（フィードの中身を HTML として入れない）")

# radiko の局名の対応（公式の局名に、見えない文字が混ざる・略称で出ている局）
import radiko
rk0 = radiko.Radiko()
rk0._stations = {"CRT": {"name": "CRT栃木放送"}, "MRO": {"name": "MROラジオ"}, "JOAK-FM": {"name": "NHK FM(東京)"}, "CRK": {"name": "ラジオ関西"}}
rk0._by_name = {radiko.norm(v["name"]): k for k, v in rk0._stations.items()}
ids = lambda head: rk0.station_ids(head + "「番組」")
check(ids("\u200b栃木放送") == ["CRT"], "局名に、見えない文字（ゼロ幅スペース）が混ざっていても合う")
check(ids("北陸放送") == ["MRO"] and ids("NHK-FM") == ["JOAK-FM"], "略称で出ている局（北陸放送→MRO）・NHK-FM")
check(ids("ラジオ関西") == ["CRK"] and ids("知らない局") == [], "いままで合っていた局は、そのまま。わからない局は飛ばす")

# ファビコン・OGP: どのページも同じ画像・同じ色。画像は static/ にある
import selectable, sitemeta, struct
tp = dt.datetime(2026, 11, 24, 12, 0, tzinfo=rss.JST)
d_out = tempfile.mkdtemp()
selectable.write_options(d_out, [])
pages = {"index": rss.index_html([], tp), "about": rss.about_page(tp), "custom": open(os.path.join(d_out, "custom/index.html"), encoding="utf-8").read()}
check(all('<meta property="og:image" content="https://hello-rss.karin-archive.com/og.png">' in h and '<meta name="twitter:card" content="summary_large_image">' in h for h in pages.values()), "OGP: どのページも、同じ画像（og.png）と large のカード")
check(all('href="/favicon.ico"' in h and 'href="/apple-touch-icon.png"' in h and '<meta name="theme-color" content="#0080cb">' in h for h in pages.values()), "ファビコン・ホーム画面のアイコン・ブラウザの色: どのページも同じ")
check(pages["index"].split("<title>")[1].split("</title>")[0] == "ハロプロ RSS アンオフィシャル" and "<h1>ハロプロ RSS アンオフィシャル</h1>" in pages["index"], "トップ: タイトルは『ハロプロ RSS アンオフィシャル』")
check("<p>ハロー！プロジェクト関連のお知らせをRSSで読む</p>" in pages["index"] and "<p>公式サイトの情報を RSS にしたものです。リーダーにURLを登録して使います。</p>" in pages["index"], "トップ: 説明の文")
check("リーダーにURLを登録して使います" in pages["about"] and "ハロー！プロジェクト関連のお知らせをRSSで読む" in pages["about"], "このサイトについて: 同じ説明が入っている")
st = os.path.join(os.path.dirname(os.path.abspath(rss.__file__)), "static")
with open(os.path.join(st, "og.png"), "rb") as f:
    hdr = f.read(24)
check(hdr[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", hdr[16:24]) == (1200, 630), "static/og.png: 1200x630 の PNG")
check(all(os.path.getsize(os.path.join(st, n)) > 100 for n in ("favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png")), "static/: ファビコンのファイルがある")

print(f"\nall {ok} ok")
