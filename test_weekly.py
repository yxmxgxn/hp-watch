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
check(not any("生放送" in n or "#!/live" in u for n, u in sl), "生放送（テレビ・ラジオ）のリンクは、付けない")
import radiko as _radiko
check(_radiko.timefree_url("IBC", "20261126163000") == "https://radiko.jp/share/?sid=IBC&t=20261126163000", "radiko のタイムフリーは、シェア用の URL（スマホでも、その番組のページに着く）")
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
# radiko の番組のページ（/r_seasons/番号）。番組表の放送の ID から、スマホ向けのエピソードのページを開いて読む
import tempfile as _tf
_calls = []
_pages = {"/mobile/events/111": '<a href="/r_seasons/10000236">番組</a>', "/mobile/events/222": "<html>番組のページが無い</html>"}
def _fake_get(url, ua=radiko.UA):
    _calls.append((url, ua))
    path = url.replace("https://radiko.jp", "")
    if path not in _pages:
        raise OSError("通信に失敗")
    return _pages[path].encode()
_orig_get, radiko._get = radiko._get, _fake_get
_orig_sleep, radiko.time.sleep = radiko.time.sleep, lambda s: None
_cache = os.path.join(_tf.mkdtemp(), "radiko_seasons.json")
rks = radiko.Radiko(_cache)
D0 = dt.date(2026, 11, 27)
pa = {"id": "111", "title": "Memories＆Discoveries (1)", "ft": "20261127050000", "ts_ng": False}
check(rks.season_url("HELLOFIVE", pa, D0) == "https://radiko.jp/r_seasons/10000236" and _calls[0][1] == radiko.MOBILE_UA, "radiko の番組のページ: エピソードのページ（スマホ向け）から、/r_seasons/番号 を読む")
n0 = len(_calls)
check(rks.season_url("HELLOFIVE", dict(pa, id="999", title="Memories＆Discoveries (2)"), D0) == "https://radiko.jp/r_seasons/10000236" and len(_calls) == n0, "同じ局・同じ番組は、(1)(2) の番号が違っても、記憶から返す（取りに行かない）")
pb = {"id": "222", "title": "歌声は風にのって (1)", "ft": "x", "ts_ng": False}
check(rks.season_url("CRK", pb, D0) is None and rks._seasons["CRK|歌声は風にのって"]["id"] == "", "番組のページが無い番組は、None（無かったことを記憶する）")
n1 = len(_calls)
check(rks.season_url("CRK", pb, D0 + dt.timedelta(days=3)) is None and len(_calls) == n1, "無かった番組は、7日たつまで、探し直さない")
check(rks.season_url("CRK", pb, D0 + dt.timedelta(days=8)) is None and len(_calls) == n1 + 1, "無かった番組は、7日あとに、探し直す")
pc = {"id": "333", "title": "通信が失敗する番組", "ft": "x", "ts_ng": False}
check(rks.season_url("TBS", pc, D0) is None and "TBS|通信が失敗する番組" not in rks._seasons, "通信の失敗は、記憶しない（次の作り直しで、またやる）")
check(rks.season_url("TBS", {"id": "", "title": "x"}, D0) is None and rks.season_url("TBS", None, D0) is None, "番組表に無い（ID が無い）ときは、None")
check(rks.save_seasons() is True and rks.save_seasons() is False and json.load(open(_cache, encoding="utf-8"))["HELLOFIVE|MEMORIES&DISCOVERIES"]["id"] == "10000236", "記憶をファイルに書く（変わったときだけ）")
rks2 = radiko.Radiko(_cache); n2 = len(_calls)
check(rks2.season_url("HELLOFIVE", pa, D0) == "https://radiko.jp/r_seasons/10000236" and len(_calls) == n2, "次の作り直しでは、記憶から返す")
radiko.SEASON_MAX_FETCH, _mf = 1, radiko.SEASON_MAX_FETCH
rks3 = radiko.Radiko(); _pages["/mobile/events/444"] = '/r_seasons/5'; _pages["/mobile/events/555"] = '/r_seasons/6'
r1 = rks3.season_url("A", {"id": "444", "title": "a"}, D0); r2 = rks3.season_url("B", {"id": "555", "title": "b"}, D0)
check(r1 and r2 is None, "1回の作り直しで取りに行く数には、上限がある")
radiko.SEASON_MAX_FETCH = _mf
radiko._get, radiko.time.sleep = _orig_get, _orig_sleep

# ラジオの行・記事のリンク: 番組のページ。引けない番組は、その回のタイムフリー
class SeasonRk(FakeRk):
    def lookup(self, sid, ft):
        return {"ft": ft, "title": "番組", "id": "1", "ts_ng": False, "corner": False}
    def season_url(self, sid, p):
        return {"TBS": "https://radiko.jp/r_seasons/1", "CRK": None, "FMT": "https://radiko.jp/r_seasons/1"}.get(sid)
    def station_ids(self, title):
        return [s for s, k in (("TBS", "TBS"), ("CRK", "関西"), ("FMT", "FM東京")) if k in title]
    def name(self, sid):
        return {"TBS": "TBSラジオ", "CRK": "ラジオ関西", "FMT": "TOKYO FM"}[sid]
    def free_area(self, sid):
        return "関東"
rk_s = SeasonRk()
TF = lambda sid: f"https://radiko.jp/share/?sid={sid}&t=20261127133000"
x_tbs = {"date": "2026-11-27", "schedule": {"start": "13:30"}, "title": "TBSラジオ「番組」"}
check(rss.radiko_link_list([x_tbs], rk_s) == [("radiko（番組ページ）", "https://radiko.jp/r_seasons/1"), ("radiko（TBSラジオ）", TF("TBS"))], "ラジオの行: 番組のページ（局名なし）と、その局のその回のタイムフリーの両方")
check(rss.radiko_link_list([x_tbs], rk_s, timefree=False) == [("radiko（番組ページ）", "https://radiko.jp/r_seasons/1")], "ラジオの行: まだ放送していない日は、番組のページだけ")
x_crk = {"date": "2026-11-27", "schedule": {"start": "13:30"}, "title": "ラジオ関西「番組」"}
check(rss.radiko_link_list([x_crk], rk_s) == [("radiko（ラジオ関西）", TF("CRK"))] and rss.radiko_link_list([x_crk], rk_s, timefree=False) == [], "ラジオの行: 番組のページが引けない番組は、タイムフリーだけ（放送前は、何も出さない）")
x_two = {"date": "2026-11-27", "schedule": {"start": "13:30"}, "title": "TBSラジオ/FM東京「番組」"}
check(rss.radiko_link_list([x_two], rk_s) == [("radiko（番組ページ）", "https://radiko.jp/r_seasons/1"), ("radiko（TBSラジオ）", TF("TBS")), ("radiko（TOKYO FM）", TF("FMT"))], "ラジオの行: 別の局でも、同じ番組のページは1つにまとめる。タイムフリーは局ごと")
x_mix = {"date": "2026-11-27", "schedule": {"start": "13:30"}, "title": "TBSラジオ/ラジオ関西「番組」"}
check(len(rss.radiko_link_list([x_mix], rk_s)) == 3, "ラジオの行: 番組のページが引ける局と、引けない局が混ざるときは、番組のページ1つ ＋ タイムフリー2つ")
lk, nt = rss.radiko_links(x_tbs, "TBSラジオ「番組」", "13:30", rk_s)
check([l[0] for l in lk] == ["radiko 番組ページ", "radiko タイムフリー（TBSラジオ・無料で聴けるのは関東）"] and lk[0][1] == "https://radiko.jp/r_seasons/1" and lk[1][1] == TF("TBS") and any("放送後1週間" in n for n in nt), "記事: 番組のページと、タイムフリー（局名・無料で聴ける範囲つき）の両方")
lk2, _ = rss.radiko_links(x_crk, "ラジオ関西「番組」", "13:30", rk_s)
check(len(lk2) == 1 and lk2[0][0].startswith("radiko タイムフリー（ラジオ関西") and "share/?sid=CRK" in lk2[0][1], "記事: 番組のページが引けなければ、タイムフリーだけ")

# 公式の局名の書き方が違う局（「FM石川」と「エフエム石川」。2026年11月に、FM石川に radiko のリンクが付かなかった）
for official, sid in (("FM石川", "HELLOFIVE"), ("エフエム石川", "HELLOFIVE"), ("FM山陰", "FM-SANIN"), ("秋田放送", "ABS"), ("信越放送", "SBC"), ("IBC岩手放送", "IBC"), ("AIR-G’", "AIR-G"), ("tbc東北放送", "TBC")):
    check(rk0.station_ids(f"{official}「番組」") == [sid], f"radiko の局: 公式の「{official}」→ {sid}")
check(rk0.station_ids("JFN「番組」") == [] and rk0.station_ids("ラジオななお/ラジオかなざわ「番組」") == [], "radiko に無い局（JFN のネット・コミュニティ FM）は、リンクを付けない")
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

# 今日の予定のページ（/today/）: 今日・これから3日先まで・これまで7日前まで
def mk(date, title, station="TBS", cat="TV", start="24:58", who="出演者"):
    return {"date": date, "category": cat, "title": {"content": f"{station}「{title}」"}, "schedule": {"start": start}, "artists": [], "status": "", "link": ""}
now_t = dt.datetime(2026, 11, 26, 12, 0, tzinfo=rss.JST)
sched_t = [mk("2026-11-18", "八日前"), mk("2026-11-19", "七日前"), mk("2026-11-25", "昨日の番組"), mk("2026-11-26", "今日の番組"), mk("2026-11-27", "明日の番組"), mk("2026-11-29", "三日後"), mk("2026-11-30", "四日後")]
days_t = rss.today_days("hp", "https://example.test", sched_t, now_t, None)
check([d["date"] for d in days_t] == ["2026-11-19", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-29"], "今日の予定のページ: 7日前から3日後まで（8日前・4日後は載せない）")
rss.TODAY_PAGES["hp"] = days_t
page_t = rss.today_page("hp", now_t)
check('<details class="day" id="d-2026-11-26" open>' in page_t and 'id="d-2026-11-29">' in page_t and 'id="d-2026-11-19">' in page_t and "八日前" not in page_t and "四日後" not in page_t, "今日の予定のページ: 今日は開いている。これから・これまでは折りたたみ。範囲の外は載せない")
check(page_t.index("今日 11/26") < page_t.index("これから（明日〜3日後）") < page_t.index("これまで（昨日〜7日前）"), "今日の予定のページ: 今日 → これから → これまで の順")
check('href="/today/jproom/"' in page_t and 'class="on">ハロプロ' in page_t and 'href="/today/upfront/"' in page_t, "今日の予定のページ: サイトの切り替え（ハロプロ・JPROOM・アップフロント）")
check('<link rel="canonical" href="https://hello-rss.karin-archive.com/today/">' in page_t and "og:image" in page_t and 'class="site-foot"' in page_t, "今日の予定のページ: 共有の画像・フッターも、ほかのページと同じ")
check(rss.today_page_url("hp", "2026-11-25").endswith("/today/#d-2026-11-25") and rss.today_page_url("jproom").endswith("/today/jproom/"), "今日の予定のページの URL")
rss.TODAY_PAGES.clear()
check('href="today/"' in rss.index_html([], now_t) and 'href="/today/"' in rss.footer_html() if hasattr(rss, "footer_html") else 'href="/today/"' in sitemeta.footer_html(), "トップ・フッターから、今日の予定のページへ")

print(f"\nall {ok} ok")
